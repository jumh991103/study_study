"""
CloudCare AI - 합성데이터 생성 6단계: renewal_churn(Target) 생성
=================================================================
modeling_snapshot(5단계 산출물)의 관측가능한 15개+2개(tenure/renewal_count) feature와,
지금까지 관측치 생성에만 부분적으로 쓰였던 은닉변수(만족도, 예산삭감확률, 경쟁사유인,
조직개편)를 함께 입력으로 받아 "이탈확률"을 계산하고, 그 확률로 실제 renewal_churn(0/1)을
샘플링한다.

★★★ 이 파일이 하는 일 = "정답(Target) 생성기" ★★★
지금까지 만든 모든 테이블은 X(입력)였고, 여기서 처음으로 y(정답)를 만든다.
- 관측가능 feature들의 효과 → 나중에 실제 모델이 "배울 수 있는" 부분
- 은닉변수들의 효과 → 나중에 실제 모델이 "절대 볼 수 없는" 부분(그래서 모델 AUC가
  1.0이 아니라 0.75~0.85 정도로 나오게 만드는 핵심 장치)
- 위 두 가지에 추가로 순수 노이즈까지 더해서, 최종적으로 "관측가능 feature만으로 학습한
  로지스틱회귀"가 대략 AUC 0.75~0.85가 나오도록 노이즈 크기를 보정(calibration)한다.

★★★ leakage 방지 원칙 ★★★
이 스크립트가 계산 과정에서 쓰는 "이탈확률"(churn_probability)과 은닉변수 값들은
모델 학습에 넘기면 절대 안 되는 값들이다. 이 파일의 출력에는 QC(검증)를 위해
임시로 남겨두지만, 7단계(leakage 컬럼 제거)에서 반드시 삭제해야 한다.

★★★ 이번 단계에서 Claude가 정한 가정 (팀 미확정 - 조정 가능) ★★★
  1. 목표 이탈률 15%: B2B SaaS 전체 벤치마크(Recurly 2025, 연 3.5%)보다는 IT서비스
     벤치마크(CustomerGauge, 연 12%)에 더 가깝게 잡되, 우리 표본의 다양성(소규모 불안정
     고객 포함)을 반영해 약간 위로 잡음. 다만 이 벤치마크들은 "월/연 단위 이탈률"이고
     우리 renewal_churn은 "계약(12~36개월) 갱신 시점의 이탈"이라 직접 비교는 근사치일 뿐.
  2. days_since_last_qbr 결측치(고객 최초 계약이라 QBR을 아직 한 번도 안 한 경우) -
     이탈확률 계산에서는 "중립"으로 처리(전체 median으로 대체) - 결측이 나쁜 고객이라서가
     아니라 시점상 이른 것뿐이라고 판단.
  3. 관측가능 feature 15개(+2개)의 방향/가중치, 은닉변수 가중치, 노이즈 크기는 전부
     Claude가 설계한 가정치. 방향(+/-)은 아래 표로 정리, 크기는 목표 이탈률과 목표
     AUC(0.75~0.85, 민혁이 이전에 확정한 목표)에 맞춰 반복 보정(calibration)함.
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

SEED = 44  # 1,2단계와 다른 seed
rng = np.random.default_rng(SEED)

INPUT_CSV = "modeling_snapshot_provisional.csv"
TARGET_CHURN_RATE = 0.15
TARGET_AUC_RANGE = (0.75, 0.85)

# --- 관측가능 feature 방향/가중치 [가정치] ---
# 부호: + 이면 값이 클수록 이탈확률↑, - 이면 값이 클수록 이탈확률↓
# 크기: "강함"(±0.7~0.9) / "중간"(±0.4~0.5) / "약함"(±0.2~0.3) 로 구분해서 부여
#   - 강함: 서비스 품질에 직접적인 문제(장애, 미해결 티켓) - 고객이 가장 즉각적으로 체감
#   - 중간: 비용/자원 낭비 인식, 관계 지속성
#   - 약함: 간접적/2차적 신호 (성장추세, 계약구조 등)
FEATURE_WEIGHTS = {
    "downtime_minutes_90d": 0.9,       # 강함 + : 장애 많을수록 이탈↑
    "unresolved_ticket_rate": 0.9,     # 강함 + : 미해결 티켓 많을수록 이탈↑
    "avg_resolution_hours": 0.5,       # 중간 + : 해결 느릴수록 이탈↑
    "idle_resource_ratio": 0.5,        # 중간 + : 낭비 인식↑ -> 이탈↑
    "budget_variance_pct": 0.5,        # 중간 + : 예산초과 불만 -> 이탈↑
    "commitment_utilization_rate": -0.3,  # 약함 - : 잘 쓰고 있으면 이탈↓
    "usage_change_3m": -0.3,           # 약함 - : 사용량 느는 중이면 이탈↓
    "cost_growth_3m": 0.3,             # 약함 + : budget_variance와 방향 겹쳐서 약하게만
    "days_since_last_qbr": 0.3,        # 약함 + : 관계 소홀 신호
    "tenure_months": -0.5,             # 중간 - : 오래된 고객일수록 이탈↓ (충성도)
    "renewal_count": -0.3,             # 약함 - : tenure와 일부 중복이라 약하게만
    "contract_term_months": -0.3,      # 약함 - : 장기계약 선택 자체가 안정 고객 신호
}

# plan lock-in 효과 [팀 확정, Story A] - z-score 아닌 직접 logit 기여
PLAN_LOGIT_EFFECT = {"Basic": 0.2, "Standard": 0.0, "Premium": -0.3}

# --- 은닉변수 가중치 [가정치] - 모델이 절대 못 보는 부분 ---
SATISFACTION_WEIGHT = -1.4   # 강함 - : 만족도 높을수록 이탈↓ (핵심 동인)
BUDGET_CUT_WEIGHT = 1.2      # budget_cut_prob(0~1 확률) 그대로 사용 - 높을수록 이탈↑
COMPETITOR_PULL_WEIGHT = 1.0  # competitor_pull(0~1) 그대로 사용 - 여기서 처음 사용
ORG_CHANGE_WEIGHT = 0.5      # org_change_flag(0/1) - 조직개편 있었으면 이탈↑

# --- 캘리브레이션 대상 ---
NOISE_SIGMA_INIT = 1.8  # 로지스틱 노이즈 초기값(반복 조정 대상)
INTERCEPT_INIT = 0.0


def _zscore(series):
    mean = series.mean()
    std = series.std()
    if std == 0 or np.isnan(std):
        return series * 0.0
    return (series - mean) / std


def compute_logit(df, noise_sigma, intercept, rng):
    z = pd.DataFrame(index=df.index)

    # 결측치(days_since_last_qbr) 중립 처리: 전체 median으로 대체(이탈확률 계산용으로만,
    # 실제 modeling_snapshot의 값 자체는 그대로 NaN 유지 - 대체값을 저장하지 않음)
    qbr_filled = df["days_since_last_qbr"].fillna(df["days_since_last_qbr"].median())

    z["downtime_minutes_90d"] = _zscore(df["downtime_minutes_90d"])
    z["unresolved_ticket_rate"] = _zscore(df["unresolved_ticket_rate"])
    z["avg_resolution_hours"] = _zscore(df["avg_resolution_hours"].fillna(df["avg_resolution_hours"].median()))
    z["idle_resource_ratio"] = _zscore(df["idle_resource_ratio"])
    z["budget_variance_pct"] = _zscore(df["budget_variance_pct"])
    z["commitment_utilization_rate"] = _zscore(df["commitment_utilization_rate"])
    z["usage_change_3m"] = _zscore(df["usage_change_3m"])
    z["cost_growth_3m"] = _zscore(df["cost_growth_3m"])
    z["days_since_last_qbr"] = _zscore(qbr_filled)
    z["tenure_months"] = _zscore(df["tenure_months"])
    z["renewal_count"] = _zscore(df["renewal_count"])
    z["contract_term_months"] = _zscore(df["contract_term_months"])

    observable_signal = sum(z[col] * w for col, w in FEATURE_WEIGHTS.items())
    plan_signal = df["managed_service_plan"].map(PLAN_LOGIT_EFFECT)

    # 은닉변수(모델이 못 보는 부분)
    satisfaction_z = (df["_customer_satisfaction_latent"] - 0.5) * 2  # [0,1] -> 대략 [-1,1]
    hidden_signal = (
        SATISFACTION_WEIGHT * satisfaction_z
        + BUDGET_CUT_WEIGHT * df["_budget_cut_prob"]
        + COMPETITOR_PULL_WEIGHT * df["_competitor_pull"]
        + ORG_CHANGE_WEIGHT * df["_org_change_flag"]
    )

    noise = rng.normal(0, noise_sigma, size=len(df))

    logit = intercept + observable_signal + plan_signal + hidden_signal + noise
    return logit, observable_signal, plan_signal


def build_observable_design_matrix(df):
    """AUC 측정용 - 실제 모델이 볼 수 있는 15개+2개 feature만으로 설계행렬 구성."""
    numeric_cols = [
        "contract_term_months", "cloud_cost_30d", "cost_growth_3m", "budget_variance_pct",
        "commitment_utilization_rate", "usage_change_3m", "idle_resource_ratio",
        "downtime_minutes_90d", "unresolved_ticket_rate", "avg_resolution_hours",
        "days_since_last_qbr", "tenure_months", "renewal_count",
    ]
    X_num = df[numeric_cols].copy()
    for col in numeric_cols:
        X_num[col] = X_num[col].fillna(X_num[col].median())
    X_num = pd.DataFrame(StandardScaler().fit_transform(X_num), columns=numeric_cols, index=df.index)

    X_cat = pd.get_dummies(
        df[["industry", "company_size", "managed_service_plan", "support_plan"]],
        drop_first=True,
    )
    return pd.concat([X_num, X_cat], axis=1)


def calibrate_and_generate(df, max_iters=15):
    noise_sigma = NOISE_SIGMA_INIT
    intercept = INTERCEPT_INIT
    X_design = build_observable_design_matrix(df)

    for iteration in range(1, max_iters + 1):
        rng_iter = np.random.default_rng(SEED + iteration)  # 매 반복 다른 노이즈 draw로 확인

        # --- intercept 이분탐색으로 목표 이탈률 맞추기 (노이즈/가중치는 고정한 채) ---
        lo, hi = -10.0, 10.0
        for _ in range(40):
            mid = (lo + hi) / 2
            logit, _, _ = compute_logit(df, noise_sigma, mid, rng_iter)
            p = 1 / (1 + np.exp(-logit))
            rate = p.mean()
            if rate < TARGET_CHURN_RATE:
                lo = mid
            else:
                hi = mid
        intercept = (lo + hi) / 2

        logit, _, _ = compute_logit(df, noise_sigma, intercept, rng_iter)
        p = 1 / (1 + np.exp(-logit))
        churn = rng_iter.binomial(1, p)

        X_train, X_test, y_train, y_test = train_test_split(
            X_design, churn, test_size=0.25, random_state=SEED, stratify=churn
        )
        clf = LogisticRegression(max_iter=1000)
        clf.fit(X_train, y_train)
        auc = roc_auc_score(y_test, clf.predict_proba(X_test)[:, 1])

        print(f"[반복 {iteration}] noise_sigma={noise_sigma:.3f}, intercept={intercept:.3f}, "
              f"실제이탈률={churn.mean():.4f}, 관측feature만 AUC={auc:.4f}")

        if TARGET_AUC_RANGE[0] <= auc <= TARGET_AUC_RANGE[1]:
            print(f"목표 AUC 범위 도달 - 반복 종료")
            break
        elif auc > TARGET_AUC_RANGE[1]:
            noise_sigma *= 1.15  # 너무 잘 맞음 -> 노이즈 키움
        else:
            noise_sigma *= 0.85  # 너무 안 맞음 -> 노이즈 줄임
    else:
        print("[경고] max_iters 안에 목표 AUC 범위 도달 못함 - 마지막 값으로 진행")

    return churn, p, noise_sigma, intercept, auc


if __name__ == "__main__":
    df = pd.read_csv(INPUT_CSV)

    churn, p, noise_sigma, intercept, auc = calibrate_and_generate(df)

    df["renewal_churn"] = churn
    df["_churn_probability"] = p.round(4)  # QC용 - 7단계에서 반드시 제거 대상(leakage)

    df.to_csv("modeling_snapshot_with_target_provisional.csv", index=False, encoding="utf-8-sig")

    print(f"\n최종 이탈률: {churn.mean():.4f} (목표 {TARGET_CHURN_RATE})")
    print(f"최종 noise_sigma={noise_sigma:.3f}, intercept={intercept:.3f}")
    print(f"관측feature만으로 학습한 로지스틱회귀 held-out AUC: {auc:.4f} (목표 {TARGET_AUC_RANGE})")

    print("\n[검증 1] plan별 이탈률 (Premium이 낮게 나오는지 - Story A 확인):")
    print(df.groupby("managed_service_plan")["renewal_churn"].mean().round(3))

    print("\n[검증 2] 은닉변수(만족도)와 실제 이탈여부 상관관계 (모델은 못 보지만 생성기가 실제로 반영했는지 확인):")
    print(df[["_customer_satisfaction_latent", "renewal_churn"]].corr().iloc[0, 1].round(3))

    print("\n[검증 3] company_size별 이탈률:")
    print(df.groupby("company_size")["renewal_churn"].mean().round(3))

    print("\n[검증 4] downtime/unresolved_ticket_rate 상위 20% vs 하위 20% 이탈률 비교 (강한 신호가 실제로 작동하는지):")
    for col in ["downtime_minutes_90d", "unresolved_ticket_rate"]:
        top20 = df[df[col] >= df[col].quantile(0.8)]["renewal_churn"].mean()
        bottom20 = df[df[col] <= df[col].quantile(0.2)]["renewal_churn"].mean()
        print(f"  {col}: 상위20% 이탈률={top20:.3f}, 하위20% 이탈률={bottom20:.3f}")

    print("\n저장 완료: modeling_snapshot_with_target_provisional.csv")
