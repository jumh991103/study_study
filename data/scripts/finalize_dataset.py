"""
CloudCare AI - 합성데이터 생성 7단계: truncate + leakage 컬럼 제거 (최종 마무리)
=================================================================
6단계까지는 모든 고객에게 "최대 시도 횟수(max_attempts)"만큼의 계약을 미리 다 만들어뒀다.
하지만 renewal_churn=1(이탈)이 발생한 계약 "이후"의 후속 계약은 현실에서는 존재할 수 없다
(이탈한 고객이 그 다음 계약을 또 맺을 리 없음) - 그래서 지금까지는 "이 고객이 이탈 안 했다면
있었을 법한" 계약까지 전부 만들어둔 상태였고, 이번 단계에서 그 논리적으로 불가능한 후속
계약들을 잘라낸다(truncate).

★★★ truncate 규칙 ★★★
고객별로 계약을 contract_seq 순서로 보고, renewal_churn=1이 처음 나온 계약까지만 남기고
그 이후 계약(및 그 계약들의 monthly_cloud_usage/monthly_support 레코드)은 전부 삭제한다.
(이탈=1인 계약 자체는 "이탈이 결정된 계약"이므로 남겨야 함 - 그 다음 계약부터가 문제)

★★★ leakage 컬럼 제거 ★★★
`_`로 시작하는 모든 컬럼(은닉변수 4종 + _baseline_monthly_cost_anchor + _churn_probability)은
전부 모델 입력이 되면 안 되는 leakage/내부용 값이므로 최종 산출물에서 제거한다.

★★★ 최종 산출물 (4개, DB 연동/모델학습용) ★★★
  - customer_contract_final.csv
  - monthly_cloud_usage_final.csv
  - monthly_support_final.csv
  - modeling_snapshot_final.csv  <- 이게 실제 ML 모델 학습에 쓰이는 최종 데이터셋
"""

import pandas as pd

contract_df = pd.read_csv("customer_contract_provisional.csv")
usage_df = pd.read_csv("monthly_cloud_usage_provisional.csv")
support_df = pd.read_csv("monthly_support_provisional.csv")
snapshot_df = pd.read_csv("modeling_snapshot_with_target_provisional.csv")

# --- 1. truncate 대상 계약 결정 ---
# customer_contract의 contract_seq를 modeling_snapshot의 renewal_churn과 조인
seq_churn = snapshot_df.merge(
    contract_df[["contract_id", "customer_id", "contract_seq"]], on=["contract_id", "customer_id"]
)[["customer_id", "contract_seq", "renewal_churn"]]

# 고객별 "첫 이탈이 발생한 contract_seq" (없으면 무한대로 취급 = 전부 유지)
first_churn_seq = (
    seq_churn[seq_churn["renewal_churn"] == 1]
    .groupby("customer_id")["contract_seq"]
    .min()
)

seq_churn["first_churn_seq"] = seq_churn["customer_id"].map(first_churn_seq).fillna(float("inf"))
seq_churn["keep"] = seq_churn["contract_seq"] <= seq_churn["first_churn_seq"]

keep_map = seq_churn.set_index(["customer_id", "contract_seq"])["keep"]
contract_df["_keep"] = contract_df.set_index(["customer_id", "contract_seq"]).index.map(keep_map)
dropped_contracts = contract_df[contract_df["_keep"] == False]["contract_id"].tolist()
keep_contract_ids = set(contract_df[contract_df["_keep"] != False]["contract_id"])

print(f"전체 계약 수: {len(contract_df)}")
print(f"이탈 이후 후속계약이라 잘라낸 계약 수: {len(dropped_contracts)}")
print(f"최종 유지 계약 수: {len(keep_contract_ids)}")

# --- 2. 4개 테이블 전부 truncate 적용 ---
contract_final = contract_df[contract_df["contract_id"].isin(keep_contract_ids)].drop(columns=["_keep"])
usage_final = usage_df[usage_df["contract_id"].isin(keep_contract_ids)]
support_final = support_df[support_df["contract_id"].isin(keep_contract_ids)]
snapshot_final = snapshot_df[snapshot_df["contract_id"].isin(keep_contract_ids)]

# --- 3. leakage 컬럼(`_`로 시작) 전부 제거 ---
def drop_leakage_cols(df):
    leak_cols = [c for c in df.columns if c.startswith("_")]
    return df.drop(columns=leak_cols), leak_cols

contract_final, contract_leak_cols = drop_leakage_cols(contract_final)
snapshot_final, snapshot_leak_cols = drop_leakage_cols(snapshot_final)

print(f"\ncustomer_contract에서 제거한 leakage 컬럼: {contract_leak_cols}")
print(f"modeling_snapshot에서 제거한 leakage 컬럼: {snapshot_leak_cols}")

# --- 4. 저장 ---
contract_final.to_csv("customer_contract_final.csv", index=False, encoding="utf-8-sig")
usage_final.to_csv("monthly_cloud_usage_final.csv", index=False, encoding="utf-8-sig")
support_final.to_csv("monthly_support_final.csv", index=False, encoding="utf-8-sig")
snapshot_final.to_csv("modeling_snapshot_final.csv", index=False, encoding="utf-8-sig")

# =====================================================================
# 검증
# =====================================================================
print("\n[검증 1] 4개 최종 테이블의 contract_id 집합이 전부 동일한지:")
sets = {
    "contract": set(contract_final["contract_id"]),
    "usage": set(usage_final["contract_id"]),
    "support": set(support_final["contract_id"]),
    "snapshot": set(snapshot_final["contract_id"]),
}
for k, v in sets.items():
    print(f"  {k}: {len(v)}행")
print("  전부 동일:", sets["contract"] == sets["usage"] == sets["support"] == sets["snapshot"])

print("\n[검증 2] 최종 leakage 컬럼(`_`로 시작) 잔존 여부:")
for name, df in [("customer_contract_final", contract_final), ("monthly_cloud_usage_final", usage_final),
                  ("monthly_support_final", support_final), ("modeling_snapshot_final", snapshot_final)]:
    leaked = [c for c in df.columns if c.startswith("_")]
    print(f"  {name}: {leaked if leaked else '없음'}")

print("\n[검증 3] truncate 전/후 renewal_churn 비율 비교:")
print(f"  truncate 전: {snapshot_df['renewal_churn'].mean():.4f}")
print(f"  truncate 후: {snapshot_final['renewal_churn'].mean():.4f}")

print("\n[검증 4] 고객별 계약이 renewal_churn=1 이후 정말 없는지 (논리적 일관성 확인):")
check = snapshot_final.merge(contract_final[["contract_id", "customer_id", "contract_seq"]], on=["contract_id", "customer_id"])
violation_count = 0
for cid, g in check.sort_values("contract_seq").groupby("customer_id"):
    churns = g["renewal_churn"].tolist()
    if 1 in churns:
        first_idx = churns.index(1)
        if any(v == 1 for v in churns[:first_idx]) or len(churns) > first_idx + 1:
            violation_count += 1
print(f"  위반 사례(이탈 이후에도 계약이 더 있음): {violation_count}건")

print("\n[검증 5] modeling_snapshot_final 최종 컬럼 목록:")
print(list(snapshot_final.columns))

print("\n저장 완료: customer_contract_final.csv, monthly_cloud_usage_final.csv, monthly_support_final.csv, modeling_snapshot_final.csv")
