"""
CloudCare AI - 합성데이터 생성 5단계: modeling_snapshot 테이블
=================================================================
customer_contract / monthly_cloud_usage / monthly_support 세 테이블을 입력으로 받아,
계약마다 "계약종료 D-60 시점" 하나의 스냅샷 행을 만든다. Sprint1이 정의한 15개 Feature +
보조변수(ACV)를 이 단계에서 산출한다.

★★★ renewal_churn(Target)은 아직 여기서 만들지 않는다 ★★★
Sprint1 10단계 워크플로우 순서상 Target은 이 스냅샷(Feature)들을 "입력"으로 받아
6단계에서 별도 계산한다. 여기서 미리 이탈여부를 정해두고 Feature를 거꾸로 맞추면
그 자체가 정답을 데이터에 심는 leakage이므로, 이 파일은 순수하게 "관측 가능한 값의
집계"만 한다.

★★★ 이번 단계에서 Claude가 새로 정한 가정 (팀 미확정 - 근거자료 없음, 조정 가능) ★★★
  1. Sprint1 원문의 "예측시점 D-60 / 관측기간 D-60 직전 180일"을 월 단위 근사로 변환:
     - 우리 monthly_* 테이블은 "월" 단위(1개월≈30일)로 생성돼 있어서 "일" 단위로 정확히
       자를 수 없음. 60일≈2개월, 180일≈6개월로 근사함.
     - cutoff_month = contract_term_months - 2  (예: 12개월 계약 → 10개월차가 D-60 시점)
     - 관측window = [cutoff_month-5, cutoff_month] (6개월, 예: 12개월 계약이면 5~10개월차)
  2. cost_growth_3m / usage_change_3m: 6개월 관측window를 앞 3개월(prior)·뒤 3개월(recent)
     로 나눠서 recent 평균 대비 prior 평균의 변화로 계산(90일 단위 트렌드 근사)
  3. commitment_utilization_rate / idle_resource_ratio: 6개월 관측window 전체 평균
     (D-60 시점의 "현재 순간값"이 아니라 "최근 상태의 안정적 추정치"로 해석)
  4. downtime_minutes_90d: 관측window의 "최근 3개월"(recent, ≈90일) 합계
  5. unresolved_ticket_rate / avg_resolution_hours: 6개월 관측window 전체 합산 기준
     (unresolved_ticket_rate = 기간내 미해결티켓수 합 / 기간내 전체티켓수 합,
      avg_resolution_hours = 기간내 총 해결시간 합 / 기간내 해결건수 합)
  6. days_since_last_qbr: "관측window 안"이 아니라 "계약 시작부터 cutoff_month까지 전체
     이력"에서 가장 최근 QBR을 찾음(QBR은 window보다 드물게 발생해서 window로 한정하면
     결측이 너무 많아짐). 이력에 QBR이 한 번도 없었으면 NaN(결측)으로 남겨둠 - 실제로
     이것도 "정보"이므로(한 번도 QBR을 안 한 고객) 임의로 큰 값을 채워넣지 않음. 결측치
     처리는 팀의 전처리 단계(6단계 워크플로우 항목)에서 다루기로 함.
  7. ACV(annual_contract_value) 계산식: Sprint1에 계산식이 없어 Claude가 새로 정의함
     (지난 논의에서 제안했던 방식 그대로): ACV = cloud_cost_30d × 12 × plan_multiplier ×
     support_multiplier. multiplier 값 자체는 "관리형 서비스가 클라우드 원가에 얹는
     프리미엄 마진"을 나타내려는 임의 가정치(근거자료 없음) - 팀 확정 필요.

★★★ 재검토 중 발견한 누락 - 수정함 (9/20) ★★★
Sprint1의 "15개 Feature" 목록에는 tenure_months/renewal_count가 없지만, 1단계에서
tenure 설계(설계B)를 확정할 때 "180일 관측window와 무관하게 계정 생성시점부터 누적해서
각 계약 스냅샷에 반영"하기로 이미 합의했었음(project_memory 기록). 최초 초안에서 이걸
깜빡하고 안 넣었다가 재검토하면서 발견해 추가함.
  - renewal_count: 계약(customer_contract) 레벨 값을 그대로 재사용(이 계약이 몇 번째
    갱신인지는 D-60 시점이든 계약 시작 시점이든 값이 안 바뀌므로 재계산 불필요)
  - tenure_months: customer_contract의 tenure_months는 "계약 시작일" 기준으로 계산돼
    있어서 그대로 쓰면 안 됨(D-60 시점은 계약 시작보다 cutoff_month개월 뒤이므로).
    tenure_at_snapshot = (계약 시작 시점의 tenure_months) + cutoff_month 로 재계산.
"""

import numpy as np
import pandas as pd

CONTRACT_CSV = "customer_contract_provisional.csv"
USAGE_CSV = "monthly_cloud_usage_provisional.csv"
SUPPORT_CSV = "monthly_support_provisional.csv"

WINDOW_MONTHS = 6   # 180일 근사
CUTOFF_LAG_MONTHS = 2  # 60일 근사

# --- ACV 배수 [가정치 - 팀 미확정] ---
ACV_PLAN_MULTIPLIER = {"Basic": 1.00, "Standard": 1.10, "Premium": 1.25}
ACV_SUPPORT_MULTIPLIER = {"Standard": 1.00, "Priority": 1.05, "Enterprise": 1.15}


def build_snapshot(contract_df, usage_df, support_df):
    usage_by_contract = {cid: g.sort_values("month_index") for cid, g in usage_df.groupby("contract_id")}
    support_by_contract = {cid: g.sort_values("month_index") for cid, g in support_df.groupby("contract_id")}
    # [9/20 추가 - 오늘 전체 재검토 중 발견한 결함] days_since_last_qbr을 원래 "이 계약
    # 안에서만" 찾고 있었는데, 이러면 tenure_months/renewal_count처럼 "계정 전체 이력"으로
    # 봐야 할 값을 계약 단위로 리셋하는 꼴이 됨 - 실제로 갱신계약(contract_seq 2,3)도
    # 첫 계약(seq=1)과 결측률이 완전히 동일(6.7~6.8%)하게 나와서 확인됨(이전 계약에서
    # 이미 QBR을 했어도 반영이 안 되고 있었음). 수정: 계약(contract_id)이 아니라
    # 고객(customer_id) 전체 이력 + 실제 날짜(month_date)로 가장 최근 QBR을 찾음.
    support_by_customer = {
        cid: g.sort_values("month_date") for cid, g in support_df.groupby("customer_id")
    }

    rows = []
    skipped_too_short = 0

    for _, c in contract_df.iterrows():
        contract_id = c["contract_id"]
        term_months = int(c["contract_term_months"])

        cutoff_month = term_months - CUTOFF_LAG_MONTHS
        window_start = cutoff_month - (WINDOW_MONTHS - 1)

        # 계약기간이 너무 짧아 관측window를 못 채우는 경우 스킵
        # (현재 TERM_PROBS_BY_SIZE는 최소 12개월이라 실제로는 발생하지 않지만,
        # 나중에 팀이 term_months 옵션에 더 짧은 기간을 추가할 경우를 대비한 방어 코드)
        if window_start < 1 or cutoff_month < 1:
            skipped_too_short += 1
            continue

        u = usage_by_contract.get(contract_id)
        s = support_by_contract.get(contract_id)
        if u is None or s is None:
            continue

        u_window = u[(u["month_index"] >= window_start) & (u["month_index"] <= cutoff_month)]
        s_window = s[(s["month_index"] >= window_start) & (s["month_index"] <= cutoff_month)]

        recent_start = cutoff_month - 2  # recent 3개월: cutoff-2 ~ cutoff
        prior_end = recent_start - 1     # prior 3개월: window_start ~ prior_end
        u_recent = u_window[u_window["month_index"] >= recent_start]
        u_prior = u_window[u_window["month_index"] <= prior_end]

        # --- cloud_cost_30d: cutoff 시점(가장 최근 달)의 비용 ---
        cloud_cost_30d = float(u_window[u_window["month_index"] == cutoff_month]["actual_monthly_cost"].iloc[0])

        # --- cost_growth_3m ---
        recent_cost_avg = u_recent["actual_monthly_cost"].mean()
        prior_cost_avg = u_prior["actual_monthly_cost"].mean()
        cost_growth_3m = float(recent_cost_avg / prior_cost_avg - 1) if prior_cost_avg > 0 else np.nan

        # --- budget_variance_pct (실제비용 vs anchor="예정 비용") ---
        anchor = float(c["_baseline_monthly_cost_anchor"])
        budget_variance_pct = float((cloud_cost_30d - anchor) / anchor * 100)

        # --- commitment_utilization_rate / idle_resource_ratio (window 평균) ---
        commitment_utilization_rate = float(u_window["commitment_utilization_rate"].mean())
        idle_resource_ratio = float(u_window["idle_resource_ratio"].mean())

        # --- usage_change_3m: commitment_utilization_rate의 recent-prior 차이(%p) ---
        recent_util_avg = u_recent["commitment_utilization_rate"].mean()
        prior_util_avg = u_prior["commitment_utilization_rate"].mean()
        usage_change_3m = float(recent_util_avg - prior_util_avg)

        # --- downtime_minutes_90d: recent 3개월 합 ---
        downtime_minutes_90d = int(u_recent["downtime_minutes"].sum())

        # --- unresolved_ticket_rate / avg_resolution_hours (window 합산 기준) ---
        total_tickets = int(s_window["ticket_count"].sum())
        total_unresolved = int(s_window["unresolved_count"].sum())
        total_resolved = int(s_window["resolved_count"].sum())
        total_res_hours = float(s_window["total_resolution_hours"].sum())

        unresolved_ticket_rate = float(total_unresolved / total_tickets) if total_tickets > 0 else np.nan
        avg_resolution_hours = float(total_res_hours / total_resolved) if total_resolved > 0 else np.nan

        # --- days_since_last_qbr: "고객 전체 이력"(이전 계약 포함) + 실제 날짜 기준 탐색 ---
        # (수정 전엔 이 계약 안에서만 찾아서 갱신고객의 QBR 이력이 매번 리셋되는 버그가 있었음)
        cutoff_date = u_window[u_window["month_index"] == cutoff_month]["month_date"].iloc[0]
        cutoff_date = pd.Timestamp(cutoff_date)
        s_customer_history = support_by_customer[c["customer_id"]]
        s_customer_history = s_customer_history[pd.to_datetime(s_customer_history["month_date"]) <= cutoff_date]
        qbr_dates = pd.to_datetime(s_customer_history[s_customer_history["had_qbr"]]["month_date"])
        if len(qbr_dates) > 0:
            last_qbr_date = qbr_dates.max()
            days_since_last_qbr = int((cutoff_date - last_qbr_date).days)
        else:
            days_since_last_qbr = np.nan  # 고객 전체 이력상 QBR이 한번도 없었음 (결측 - 팀 전처리 단계에서 처리)

        # --- ACV (보조변수, Feature 아님) ---
        plan = c["managed_service_plan"]
        support_plan = c["support_plan"]
        acv = float(cloud_cost_30d * 12 * ACV_PLAN_MULTIPLIER[plan] * ACV_SUPPORT_MULTIPLIER[support_plan])

        rows.append(
            {
                "contract_id": contract_id,
                "customer_id": c["customer_id"],
                # --- 15개 Feature ---
                "industry": c["industry"],
                "company_size": c["company_size"],
                "managed_service_plan": plan,
                "support_plan": support_plan,
                "contract_term_months": term_months,
                # Sprint1 15개 목록엔 없지만 1단계에서 팀이 별도로 확정한 tenure 설계 반영
                # (계정 생성시점부터 누적, D-60 시점 기준으로 재계산 - 위 주석 참고)
                "tenure_months": int(c["tenure_months"]) + cutoff_month,
                "renewal_count": int(c["renewal_count"]),
                "cloud_cost_30d": round(cloud_cost_30d),
                "cost_growth_3m": round(cost_growth_3m, 4) if not np.isnan(cost_growth_3m) else np.nan,
                "budget_variance_pct": round(budget_variance_pct, 2),
                "commitment_utilization_rate": round(commitment_utilization_rate, 4),
                "usage_change_3m": round(usage_change_3m, 4),
                "idle_resource_ratio": round(idle_resource_ratio, 4),
                "downtime_minutes_90d": downtime_minutes_90d,
                "unresolved_ticket_rate": round(unresolved_ticket_rate, 4) if not np.isnan(unresolved_ticket_rate) else np.nan,
                "avg_resolution_hours": round(avg_resolution_hours, 2) if not np.isnan(avg_resolution_hours) else np.nan,
                "days_since_last_qbr": days_since_last_qbr,
                # --- 보조변수 (Feature 아님) ---
                "annual_contract_value": round(acv),
                # --- 6단계(Target 생성)용으로 임시 보존, 7단계에서 반드시 제거해야 하는 leakage 대상 ---
                "_customer_satisfaction_latent": c["_customer_satisfaction_latent"],
                "_budget_cut_prob": c["_budget_cut_prob"],
                "_competitor_pull": c["_competitor_pull"],
                "_org_change_flag": c["_org_change_flag"],
            }
        )

    if skipped_too_short:
        print(f"[경고] 관측window를 못 채워 스킵된 계약 수: {skipped_too_short}")

    return pd.DataFrame(rows)


if __name__ == "__main__":
    contract_df = pd.read_csv(CONTRACT_CSV)
    usage_df = pd.read_csv(USAGE_CSV)
    support_df = pd.read_csv(SUPPORT_CSV)

    snapshot_df = build_snapshot(contract_df, usage_df, support_df)
    snapshot_df.to_csv("modeling_snapshot_provisional.csv", index=False, encoding="utf-8-sig")

    print(f"modeling_snapshot: {len(snapshot_df)}행 (계약 {contract_df['contract_id'].nunique()}건 중)")

    print("\n[검증 1] 컬럼별 결측치 비율:")
    print(snapshot_df.isna().mean().round(4))

    print("\n[검증 2] 15개 Feature 요약통계 (수치형):")
    numeric_cols = [
        "cloud_cost_30d", "cost_growth_3m", "budget_variance_pct",
        "commitment_utilization_rate", "usage_change_3m", "idle_resource_ratio",
        "downtime_minutes_90d", "unresolved_ticket_rate", "avg_resolution_hours",
        "days_since_last_qbr",
    ]
    print(snapshot_df[numeric_cols].describe().round(3).T)

    print("\n[검증 3] plan별 downtime_minutes_90d / annual_contract_value 평균 (방향성 확인):")
    print(snapshot_df.groupby("managed_service_plan")[["downtime_minutes_90d", "annual_contract_value"]].mean().round(0))

    print("\n[검증 4] budget_variance_pct 분포 (0 근처에 몰려있는지 - anchor 대비 실제비용 변동폭):")
    print(snapshot_df["budget_variance_pct"].describe().round(2))

    print("\n저장 완료: modeling_snapshot_provisional.csv")
