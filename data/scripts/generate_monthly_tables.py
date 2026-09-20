"""
CloudCare AI - 합성데이터 생성 2~3단계: monthly_cloud_usage, monthly_support 테이블
=================================================================================
1단계 산출물(customer_contract_provisional.csv)을 입력으로 받아, 각 계약(contract_id)의
계약기간(contract_term_months) 동안의 "월별" 이력 2개 테이블을 만든다.

★★★ 이 테이블도 "잠정본"입니다 ★★★
customer_contract와 마찬가지로 renewal_churn이 아직 없는 상태의 계약 이력 전체 기간에
대해 만들어짐. 6단계(Target 생성)에서 실제 churn을 계산한 뒤, "이탈 이후 시점"의 월별
레코드는 truncate 처리할 예정(지금은 만들지 않음 - 그때 가서 이 테이블의 뒤쪽을 잘라냄).

★★★ 이번 단계에서 Claude가 새로 임의로 가정한 값들 (팀 미확정 - 조정 가능) ★★★
  1. INTRA_CONTRACT_COST_TREND/NOISE: 계약 "내부"에서의 월별 비용 변동폭
     (계약과 계약 "사이"의 변동은 이미 1단계 COST_DRIFT_MEAN/SIGMA로 처리했음 - 이건 그것과
     다른, 한 계약 안에서 매달 미세하게 오르내리는 정도)
  2. commitment_utilization_rate, idle_resource_ratio, downtime_minutes, 티켓 지표들을
     하나의 공유 잠재변수(ops_latent)로 묶은 것 - 1단계에서 팀이 확정한 "feature 클러스터당
     공유 잠재변수 설명력 40~60%" 설계를 여기 그대로 적용

  [9/20 재수정 - 5단계 산출물을 팀원에게 설명하다가 발견한 결함]
  1단계에서 팀이 확정한 클러스터는 "비용/사용량"과 "지원품질" 2개였는데, 최초 버전에서는
  ops_latent를 commitment_utilization_rate/idle_resource_ratio/downtime/티켓에만 연결하고
  "비용"(cost_growth_3m, budget_variance_pct의 원천이 되는 actual_monthly_cost)은 완전히
  독립적인 별개 프로세스로 만들어놓음 - 그래서 modeling_snapshot에서 실제 상관관계를
  검증해보니 목표(0.6~0.75)와 다르게 비용/사용량 클러스터 내부 상관관계가 거의 0이 나왔고,
  지원품질 클러스터도 0.27~0.47로 목표에 못 미쳤음(가중치 0.5로는 부족).
  수정: (a) actual_monthly_cost의 월별 성장률도 ops_latent에 연동(운영건강도가 나쁠수록
  비용이 더 튀게) (b) OPS_LATENT_WEIGHT를 0.5→0.7로 올리고 각 지표의 독립 noise를 줄여서
  공유설명력을 높임. 단, cloud_cost_30d "원가 수준" 자체(원)은 기업규모가 압도적으로 지배하는
  값이라 ops_latent를 아무리 강하게 연결해도 상관관계가 0.6~0.75까지 오르지 않음 - 이건
  cloud_cost_30d의 스케일이 기업규모(수백만원~수억원) 차이에서 나오는 게 맞고, "성장률/비율"
  계열 파생변수(cost_growth_3m, budget_variance_pct, usage_change_3m,
  commitment_utilization_rate, idle_resource_ratio)끼리만 상관관계 목표를 맞추는 게
  올바른 해석이라고 판단함(비용 원가 수준과 운영건강도가 0.7 상관이어야 한다는 건 애초에
  비현실적 - 대기업은 원가가 커도 운영을 잘 할 수 있음)
  3. QBR(분기 사업 리뷰) 주기를 support_plan 등급에 따라 다르게 설정 - 실제 CloudCare AI가
     이렇게 운영한다는 근거자료는 없고, "고급 지원등급일수록 정기 미팅이 잦다"는 일반적인
     B2B 고객성공(CS) 관행을 그대로 가정한 것
  4. 이번 단계는 아직 renewal_churn을 모르므로, "이탈 징후"를 직접 만들지 않고 대신
     은닉변수(ops_latent, budget_cut_prob, org_change_flag)가 관측가능한 운영지표
     (idle_resource_ratio↑, downtime↑, 미해결티켓↑ 등)에 스며들도록만 설계함 - 실제
     churn 확률과 라벨은 6단계에서 이 관측치들을 입력으로 별도 계산할 예정(여기서 미리
     "이 고객은 이탈할거니까 downtime을 높게"처럼 답을 역산해서 넣지 않음 - 그러면 그 자체가
     leakage를 심어놓는 꼴이 됨)
"""

import numpy as np
import pandas as pd
from datetime import timedelta

SEED = 43  # 1단계와 다른 seed 사용 (같은 42를 계속 재사용하면 서로 다른 확률변수들 사이에
# 의도치 않은 상관이 생길 수 있어서 - 재현성은 각 단계별 고정 seed로 여전히 보장됨)
rng = np.random.default_rng(SEED)

INPUT_CSV = "customer_contract_provisional.csv"

# =====================================================================
# 0. 설정값 (Config)
# =====================================================================

# --- 계약 "내부" 월별 비용 변동 [가정치] ---
# (계약과 계약 "사이"의 변동은 1단계 COST_DRIFT_MEAN/SIGMA로 이미 처리했음 - 이건 완전히
# 다른 것: 한 계약이 진행되는 동안 매달 사용량이 자연스럽게 오르내리는 폭)
INTRA_CONTRACT_COST_TREND = 0.004   # 월 평균 0.4% 완만한 상승(사용량 자연증가 가정)
INTRA_CONTRACT_COST_NOISE_SIGMA = 0.04  # 월별 비용 노이즈(4%, 9/20 축소 - ops_latent 비중 확보용)

# --- 운영 상태 공유 잠재변수(ops_latent) 가중치 [팀 확정 설계를 그대로 적용, 9/20 가중치 상향] ---
# 1단계에서 팀이 합의한 "feature 클러스터당 공유 잠재변수 설명력 40~60%, 클러스터 내
# 상관관계 0.6~0.75" 원칙을 여기 적용. customer_satisfaction_latent(고객레벨 은닉변수)를
# 표준정규화해서 ops_latent의 "고객 특성" 축으로 쓰고, 나머지는 매달 독립적인 신규 noise.
# [9/20] 처음 0.5로 뒀더니 (6개월치를 평균/합산으로 집계한 뒤 측정한) 실제 클러스터 내
# 상관관계가 0.27~0.47로 목표(0.6~0.75)에 못 미쳤음 - 0.7로 올리고 아래 각 지표의 독립
# noise도 함께 줄여서 재조정함(재검증 결과는 스크립트 실행 로그 참고)
OPS_LATENT_WEIGHT = 0.8  # [9/20 2차 조정] 0.7로도 부족해서 재상향(아래 로그 참고)

# --- 비용 성장률의 ops_latent 연동 [9/20 추가 - 재검토로 발견한 누락 수정] ---
# 처음엔 비용을 ops_latent와 완전히 무관하게 만들어서 "비용/사용량" 클러스터 내부
# 상관관계가 사실상 0이 나왔음. 운영건강도가 나쁠수록(ops_latent 낮을수록) 비용도 더
# 튀게(과다청구/비효율적 사용으로 비용 증가) 연동함.
# [9/20 2차 조정] 0.05로는 상관관계가 0.03~0.13에 그쳐서 0.12로 올리고, 아래
# INTRA_CONTRACT_COST_NOISE_SIGMA도 0.06→0.04로 낮춰 상대적 비중을 높임
COST_OPS_LATENT_EFFECT = 0.12  # ops_latent 1단위당 월별 비용 성장률에 미치는 영향

# --- commitment_utilization_rate / idle_resource_ratio [가정치] ---
# 두 지표 모두 ops_latent가 좋을수록(운영을 잘 할수록) "위탁한 만큼 알뜰하게 쓴다"는
# 방향으로 상관되게 설계(완전 종속은 아니고 독립 noise도 섞음 - 9/20 비중 상향에 맞춰 축소)
COMMITMENT_UTIL_BASE_MEAN = 0.72
COMMITMENT_UTIL_LATENT_EFFECT = 0.16
COMMITMENT_UTIL_NOISE_SIGMA = 0.09

IDLE_RATIO_BASE_MEAN = 0.22
IDLE_RATIO_LATENT_EFFECT = -0.13  # ops_latent가 높을수록(운영 잘할수록) idle이 낮아짐(음의 상관)
IDLE_RATIO_NOISE_SIGMA = 0.07

# --- downtime/티켓 관련 [가정치] ---
# managed_service_plan(관리형 서비스 등급)이 높을수록 사전 예방적 관리가 잘 되어 장애가
# 적다고 가정(운영 latent와는 별개 경로) - "돈을 더 낼수록 장애가 적다"는 것도 lock-in/
# 만족도 스토리와 방향이 맞음(Premium=관리 잘 됨=만족도↑=이탈↓ 이라는 인과사슬과 일관)
DOWNTIME_BASE_MINUTES_BY_PLAN = {"Basic": 40, "Standard": 22, "Premium": 10}
DOWNTIME_LATENT_EFFECT = 0.7  # ops_latent 1 표준편차당 downtime이 exp(-0.7)배로 변함 (9/20 2차 상향)

# 티켓 발생량 [가정치] - support_plan(지원등급) 자체보다는 "기술적으로 얼마나 문제가
# 많은 고객인가(ops_latent)"가 티켓 수의 주 원인이라고 가정(지원등급은 "해결 속도"에만 영향)
TICKET_BASE_RATE_PER_MONTH = 2.6
TICKET_LATENT_EFFECT = 0.65  # 9/20 2차 상향 (OPS_LATENT_WEIGHT 상향에 맞춤)

# support_plan별 해결속도/미해결율 [가정치 - Sprint1/AWS자료 근거 없음, 일반적인 SLA
# 등급 관행(고급일수록 빠르고 확실하게 처리)만 반영]
RESOLUTION_PARAMS_BY_SUPPORT = {
    "Standard":   {"mean_hours": 48, "sigma": 0.6, "unresolved_rate_base": 0.16},
    "Priority":   {"mean_hours": 20, "sigma": 0.5, "unresolved_rate_base": 0.08},
    "Enterprise": {"mean_hours": 6,  "sigma": 0.5, "unresolved_rate_base": 0.03},
}
UNRESOLVED_LATENT_EFFECT = 0.12  # ops_latent 나쁠수록(음수) 미해결율 증가 (9/20 2차 상향)

# --- QBR(분기 사업 리뷰) 주기 [가정치 - CS 업계 일반 관행 가정, CloudCare AI 자체 근거 없음] ---
QBR_INTERVAL_MONTHS_BY_SUPPORT = {"Standard": 6, "Priority": 4, "Enterprise": 3}
QBR_ON_SCHEDULE_PROB = 0.8  # 예정된 달에 실제로 QBR이 열릴 확률(나머지는 밀림 - 현실적 결측 반영)


def _std_normal_from_prob(p):
    """[0,1] 범위 확률/스케일 값을 표준정규분포 근사 z-score로 변환.
    (customer_satisfaction_latent는 0~1 스케일 은닉변수인데, ops_latent 합성에는
    표준정규 스케일(-대략 -1~1)로 바꿔써야 이후 latent_effect 계수들의 의미가 일관됨)"""
    return (p - 0.5) * 2.0


def _sample_ops_latent(customer_satisfaction_latent):
    """월별 운영건강도 잠재변수. 고객레벨 은닉변수(만족도)를 공유축으로 삼고
    나머지는 매달 새로 뽑는 독립 noise (공유설명력 OPS_LATENT_WEIGHT=0.5)."""
    shared = _std_normal_from_prob(customer_satisfaction_latent)
    fresh = rng.normal(0, 1)
    return OPS_LATENT_WEIGHT * shared + (1 - OPS_LATENT_WEIGHT) * fresh


def generate_monthly_tables(contract_df: pd.DataFrame):
    usage_rows = []
    support_rows = []

    for _, c in contract_df.iterrows():
        contract_id = c["contract_id"]
        customer_id = c["customer_id"]
        plan = c["managed_service_plan"]
        support_plan = c["support_plan"]
        term_months = int(c["contract_term_months"])
        anchor = float(c["_baseline_monthly_cost_anchor"])
        satisfaction = float(c["_customer_satisfaction_latent"])
        contract_start = pd.Timestamp(c["contract_start_date"])

        res_params = RESOLUTION_PARAMS_BY_SUPPORT[support_plan]
        qbr_interval = QBR_INTERVAL_MONTHS_BY_SUPPORT[support_plan]
        downtime_base = DOWNTIME_BASE_MINUTES_BY_PLAN[plan]

        for m in range(1, term_months + 1):
            month_date = contract_start + pd.DateOffset(months=m - 1)
            ops_latent = _sample_ops_latent(satisfaction)

            # --- 비용 (계약 내부 추세 + 노이즈 + ops_latent 연동) ---
            # [9/20 추가] ops_latent가 나쁠수록(운영건강도 낮을수록) 비용도 더 튀도록 연동
            # (비효율적 사용/과다청구 등으로 예산초과 폭이 커진다는 스토리) - "비용/사용량"
            # 클러스터 내부 상관관계를 만들기 위한 핵심 연결고리
            trend = INTRA_CONTRACT_COST_TREND * (m - 1)
            noise = rng.normal(0, INTRA_CONTRACT_COST_NOISE_SIGMA)
            cost_latent_effect = -COST_OPS_LATENT_EFFECT * ops_latent
            actual_monthly_cost = anchor * np.exp(trend + noise + cost_latent_effect)

            # --- commitment_utilization_rate / idle_resource_ratio ---
            commitment_utilization_rate = float(
                np.clip(
                    rng.normal(
                        COMMITMENT_UTIL_BASE_MEAN + COMMITMENT_UTIL_LATENT_EFFECT * ops_latent,
                        COMMITMENT_UTIL_NOISE_SIGMA,
                    ),
                    0.0,
                    1.3,  # 1을 넘으면 "약정한 용량보다 더 많이 씀"(초과사용) - 현실적으로 가능해서 허용
                )
            )
            idle_resource_ratio = float(
                np.clip(
                    rng.normal(
                        IDLE_RATIO_BASE_MEAN + IDLE_RATIO_LATENT_EFFECT * ops_latent,
                        IDLE_RATIO_NOISE_SIGMA,
                    ),
                    0.0,
                    0.9,
                )
            )

            # --- downtime (장애시간, 분) ---
            downtime_lambda = downtime_base * np.exp(-DOWNTIME_LATENT_EFFECT * ops_latent)
            downtime_minutes = int(rng.poisson(lam=max(downtime_lambda, 0.1)))

            usage_rows.append(
                {
                    "contract_id": contract_id,
                    "customer_id": customer_id,
                    "month_index": m,
                    "month_date": month_date.date().isoformat(),
                    "actual_monthly_cost": round(actual_monthly_cost),
                    "commitment_utilization_rate": round(commitment_utilization_rate, 4),
                    "idle_resource_ratio": round(idle_resource_ratio, 4),
                    "downtime_minutes": downtime_minutes,
                }
            )

            # --- 지원 티켓 ---
            ticket_lambda = TICKET_BASE_RATE_PER_MONTH * np.exp(-TICKET_LATENT_EFFECT * ops_latent)
            ticket_count = int(rng.poisson(lam=max(ticket_lambda, 0.05)))

            unresolved_rate = float(
                np.clip(
                    res_params["unresolved_rate_base"] - UNRESOLVED_LATENT_EFFECT * ops_latent,
                    0.0,
                    0.9,
                )
            )
            unresolved_count = int(rng.binomial(ticket_count, unresolved_rate)) if ticket_count > 0 else 0
            resolved_count = ticket_count - unresolved_count

            if resolved_count > 0:
                mu = np.log(res_params["mean_hours"])
                resolution_hours_each = rng.lognormal(mean=mu, sigma=res_params["sigma"], size=resolved_count)
                total_resolution_hours = float(resolution_hours_each.sum())
            else:
                total_resolution_hours = 0.0

            had_qbr = False
            if m % qbr_interval == 0:
                had_qbr = bool(rng.random() < QBR_ON_SCHEDULE_PROB)

            support_rows.append(
                {
                    "contract_id": contract_id,
                    "customer_id": customer_id,
                    "month_index": m,
                    "month_date": month_date.date().isoformat(),
                    "ticket_count": ticket_count,
                    "resolved_count": resolved_count,
                    "unresolved_count": unresolved_count,
                    "total_resolution_hours": round(total_resolution_hours, 1),
                    "had_qbr": had_qbr,
                }
            )

    return pd.DataFrame(usage_rows), pd.DataFrame(support_rows)


if __name__ == "__main__":
    contract_df = pd.read_csv(INPUT_CSV)

    usage_df, support_df = generate_monthly_tables(contract_df)

    usage_df.to_csv("monthly_cloud_usage_provisional.csv", index=False, encoding="utf-8-sig")
    support_df.to_csv("monthly_support_provisional.csv", index=False, encoding="utf-8-sig")

    print(f"monthly_cloud_usage: {len(usage_df)}행 (계약 수: {usage_df['contract_id'].nunique()})")
    print(f"monthly_support: {len(support_df)}행")

    print("\n[검증 1] plan별 월평균 downtime_minutes (Premium이 낮게 나오는지):")
    merged = usage_df.merge(
        contract_df[["contract_id", "managed_service_plan", "support_plan"]], on="contract_id"
    )
    print(merged.groupby("managed_service_plan")["downtime_minutes"].mean().round(1))

    print("\n[검증 2] commitment_utilization_rate / idle_resource_ratio 요약:")
    print(usage_df[["commitment_utilization_rate", "idle_resource_ratio"]].describe().round(3))

    print("\n[검증 3] support_plan별 평균 해결시간(시간) 및 미해결율:")
    merged_s = support_df.merge(
        contract_df[["contract_id", "support_plan"]], on="contract_id"
    )
    merged_s["avg_resolution_hours"] = merged_s["total_resolution_hours"] / merged_s["resolved_count"].replace(0, np.nan)
    merged_s["unresolved_rate"] = merged_s["unresolved_count"] / merged_s["ticket_count"].replace(0, np.nan)
    print(merged_s.groupby("support_plan")[["avg_resolution_hours", "unresolved_rate"]].mean().round(2))

    print("\n[검증 4] cost_growth 신호가 있는지 (계약별 1개월차 vs 마지막달 비용 비율):")
    first_last = usage_df.groupby("contract_id")["actual_monthly_cost"].agg(["first", "last"])
    growth_ratio = (first_last["last"] / first_last["first"])
    print(growth_ratio.describe().round(3))

    print("\n[검증 5] QBR 발생 빈도 (support_plan별 월평균 had_qbr 비율):")
    merged_qbr = support_df.merge(contract_df[["contract_id", "support_plan"]], on="contract_id")
    print(merged_qbr.groupby("support_plan")["had_qbr"].mean().round(3))

    print("\n저장 완료: monthly_cloud_usage_provisional.csv, monthly_support_provisional.csv")
