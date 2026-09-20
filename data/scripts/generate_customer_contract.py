"""
CloudCare AI - 합성데이터 생성 1단계: customer_contract 테이블
=================================================================
민혁-Claude 논의 결과를 바탕으로 만든 1단계 스크립트.
이 파일만 실행하면 customer_contract.csv 가 생성됨.

★★★ 이 테이블은 "잠정본"입니다 ★★★
renewal_churn(이탈 여부)은 아직 계산하지 않습니다. 이유:
  - churn 확률은 monthly_cloud_usage / monthly_support 테이블에서 나오는
    실제 사용량·장애·지원이력 feature(cost_growth_3m, downtime 등)에 의존하는데,
    그 테이블들은 다음 단계(2~3단계)에서 만들어짐.
  - 그래서 여기서는 "고객당 계약을 최대 몇 번까지 시도했는지"만 넉넉하게(MAX_ATTEMPTS)
    미리 만들어두고, 6단계(Target 생성)에서 실제 churn을 계산한 뒤
    "첫 이탈이 발생한 계약 이후 것들은 잘라내는" 후처리(truncate)를 할 예정입니다.
    즉 지금 만드는 계약 이력은 "최대 가능치"이고, 최종본은 나중에 확정됩니다.

★★★ 이번 단계에서 팀 미확정 상태로 Claude가 임의로 가정한 값들 ★★★
(발표/문서에서 "팀 합의 아님, 가정치"라고 명시해야 하는 부분 - 코드 내 주석에도 표시)
  1. N_CUSTOMERS = 800명 (아직 팀에서 정한 적 없는 숫자, 조정 가능)
  2. industry 카테고리와 비중 (Sprint1에 업종별 비중 자료 없음)
  3. contract_term_months 분포 (12/24/36개월 비중, 기업규모별 가중치)
  4. support_plan 카테고리(Standard/Priority/Enterprise)와 managed_service_plan과의 상관관계
  5. industry가 churn에 미치는 영향 (아직 미정 - 이번 버전에서는 영향 없음/설명용 변수로만 둠)

★★★ 지난 논의에서 발견한 설계 순서 오류를 이번에 바로잡음 ★★★
기존 팀 메모의 DAG는 "company_size → plan → cost" 순서였는데,
같은 대화에서 나온 결론(AWS는 "월 사용요금 규모"로 플랜을 나눈다 → 기업규모→예상비용→플랜의
2단계 인과구조가 더 현실적)과 모순되어 있었음.
이 스크립트는 후자를 따라 "company_size → baseline_monthly_cost_anchor → managed_service_plan"
순서로 생성함. (참고: 실제 월별 비용의 "시계열"은 다음 단계인 monthly_cloud_usage에서
이 anchor 값을 중심으로 추이·노이즈를 더해 만들 예정. 여기 anchor 컬럼은 다음 단계 생성을 위한
내부 연결용이며, 최종 15개 Feature에는 포함되지 않음(cloud_cost_30d와 중복이라 제외).)
"""

import numpy as np
import pandas as pd
from datetime import timedelta

# =====================================================================
# 0. 설정값 (Config) - 팀 피드백 오면 여기 숫자만 바꾸면 됨
# =====================================================================

SEED = 42  # 재현성을 위한 고정 seed. 팀원 누가 돌려도 같은 결과 나옴.
rng = np.random.default_rng(SEED)

# [가정치, 9/20 수정] 처음엔 800으로 잡았었는데, 부트캠프 실습 기준(최소 1,000행,
# 권장 수천 행)에 비춰보면 부족했음. 특히 300인이상 대기업 고객이 전체의 2.46%뿐이라
# 800명 기준으로는 그 그룹 표본이 20~30건밖에 안 나와 학습에 불안정함.
# 3,000명으로 올리면 전체 레코드 약 5,000건, 대기업 그룹도 약 120~130건 정도 확보됨.
N_CUSTOMERS = 3000

TODAY = pd.Timestamp("2026-09-19")  # 데이터 생성 기준 시점(오늘)

# --- company_size 분포: KOSIS 원본 비중 × 팀 합의한 가중치 ---
# [실측 검증 완료 - 9/20] 기존엔 Sprint1 문서의 "78%/19%/2.5%/0.5%"를 그대로 썼는데,
# 이 수치는 출처가 불명확했고(중기부의 "1인기업이 중소기업의 78.4%" 통계와 혼동된 것으로 추정),
# 민혁이 KOSIS(국가데이터처, 통계표ID: DT_1K52D03, "시도·산업·종사자규모별 사업체수",
# 2023년 기준, 전국/전체산업)에서 직접 엑셀을 받아 실제 원본 수치로 교체함.
# 원본 사업체수(2023, 전국/전체산업, 총 6,246,489개):
#   1-4명 5,395,505 / 5-9명 485,839 / 10-19명 200,594 / 20-49명 110,170 /
#   50-99명 33,944 / 100-299명 15,817 / 300-499명 2,288 / 500-999명 1,438 / 1000명이상 894
# 위 세부 구간을 우리 4단계로 합산한 결과 (기존 가정치와 실제 차이가 꽤 큼 - 특히 50인 이상 구간이
# 훨씬 더 희소함: 300인이상은 0.5%가 아니라 0.074%로 약 7배 작음):
KOSIS_RAW_SHARE = {
    "1-4인": 0.8638,
    "5-49인": 0.1275,
    "50-299인": 0.00797,
    "300인이상": 0.00074,
}
SIZE_WEIGHT = {
    "1-4인": 0.0,
    "5-49인": 0.15,
    "50-299인": 0.55,
    "300인이상": 0.80,
}


def _company_size_probs():
    """KOSIS 원본 비중에 팀 합의 가중치를 곱한 뒤 재정규화."""
    weighted = {k: KOSIS_RAW_SHARE[k] * SIZE_WEIGHT[k] for k in KOSIS_RAW_SHARE}
    total = sum(weighted.values())
    return {k: v / total for k, v in weighted.items()}


COMPANY_SIZE_PROBS = _company_size_probs()

# --- industry 분포 [가정치 - 팀 미확정, 근거자료 없어 임의 배분] ---
INDUSTRY_PROBS = {
    "IT/소프트웨어": 0.22,
    "제조업": 0.20,
    "유통/커머스": 0.16,
    "금융/보험": 0.12,
    "물류/운송": 0.10,
    "교육": 0.08,
    "의료/헬스케어": 0.07,
    "기타서비스업": 0.05,
}

# --- 규모별 월 클라우드비용 로그정규분포 [팀 확정, 보류값] ---
# 평균(원)과 sigma. sigma=0.6은 "5인 vs 100인 100쌍 비교 시 역전 3~5%" 합의를 역산한 값.
COST_LOGNORMAL_PARAMS = {
    "5-49인": {"mean_krw": 3_000_000, "sigma": 0.6},
    "50-299인": {"mean_krw": 15_000_000, "sigma": 0.6},
    "300인이상": {"mean_krw": 60_000_000, "sigma": 0.6},
}

# --- 비용 -> 플랜 배정 확률 [팀 확정, 보류값] ---
COST_TO_PLAN_PROBS = [
    # (비용 상한, {plan: prob})
    (10_000_000, {"Basic": 0.70, "Standard": 0.25, "Premium": 0.05}),
    (50_000_000, {"Basic": 0.20, "Standard": 0.50, "Premium": 0.30}),
    (np.inf, {"Basic": 0.05, "Standard": 0.25, "Premium": 0.70}),
]

# --- support_plan: managed_service_plan과 상관 있지만 완전히 종속은 아님 ---
# [가정치] plan별로 support 등급이 확률적으로 따라가되, 어느 정도 독립적 편차를 줌
# (이렇게 해야 두 feature가 100% 중복 정보가 되는 걸 방지 - EDA에서 상관 0.6~0.7 정도 나오게)
PLAN_TO_SUPPORT_PROBS = {
    "Basic": {"Standard": 0.70, "Priority": 0.25, "Enterprise": 0.05},
    "Standard": {"Standard": 0.30, "Priority": 0.50, "Enterprise": 0.20},
    "Premium": {"Standard": 0.10, "Priority": 0.30, "Enterprise": 0.60},
}

# --- contract_term_months 분포 [가정치 - 팀 미확정] ---
# 기업 규모가 클수록 장기계약 비중이 높다는 일반적 B2B 관행을 반영(가정)
TERM_PROBS_BY_SIZE = {
    "5-49인": {12: 0.60, 24: 0.30, 36: 0.10},
    "50-299인": {12: 0.40, 24: 0.40, 36: 0.20},
    "300인이상": {12: 0.25, 24: 0.40, 36: 0.35},
}

# --- 고객당 최대 계약 시도 횟수(잠정) ---
# 실제로 몇 번째 계약까지 살아남을지는 6단계 churn 생성 후 truncate로 결정됨.
# 여기서는 "최대 이만큼까지는 만들어둔다"는 상한만 정함.
MAX_ATTEMPTS_PROBS = {1: 0.50, 2: 0.30, 3: 0.20}

# --- 은닉변수(latent) 파라미터 ---
# 모델이 절대 볼 수 없는 정보. 첫 계약에서 새로 뽑고, 이후 계약에서는
# "이전 값의 영향을 어느정도 유지하되(customer는 갑자기 딴사람이 되지 않으므로) 조금씩 변함"
LATENT_PERSISTENCE = 0.7  # 이전 계약 값 유지 비중(나머지 0.3은 새로 뽑음)

# --- 비용 anchor의 계약간 지속성 파라미터 [9/20 추가 - 재검토 중 발견한 결함 수정] ---
# 기존엔 계약(contract_seq)마다 baseline_monthly_cost_anchor를 완전히 새로 뽑았는데,
# 그러면 같은 고객이 1차 계약 Basic -> 2차 계약 Premium처럼 매번 랜덤하게 플랜이
# 리셋되는 비현실적인 결과가 나옴 (Story A "락인 효과" 설계와도 모순됨: 플랜이
# 계약마다 무작위로 바뀌면 락인이라는 개념이 성립 안 함).
# 수정: 두 번째 계약부터는 "이전 계약 비용 anchor × 성장률"로 이어지게 함.
# [9/20 추가 - 오늘 대화/파일 전체 재검토 중 발견한 결함] generate_monthly_tables.py(2단계)는
# "계약 내부"에서 매달 INTRA_CONTRACT_COST_TREND(0.4%/월)만큼 비용이 완만히 오르도록
# 만들어져 있는데, 여기 customer_contract.py(1단계)의 계약갱신 시 성장률(COST_DRIFT_MEAN)은
# "이전 계약의 anchor(=계약 시작월 기준 비용)"에서 다시 성장률을 적용하는 방식이라, 이전
# 계약이 진행되는 동안 누적된 월별 상승분을 반영하지 못함. 그 결과 매 계약 경계마다 실제
# 비용이 평균 약 9% "뚝 떨어지는" 부자연스러운 불연속이 생기는 걸 실제 데이터로 확인함
# (다음계약 첫달 비용 / 이전계약 마지막달 비용 비율 평균 0.91, 최대 계약 term이 길수록
# 더 심함). 수정: 이전 계약의 term_months 동안 2단계에서 누적됐을 것으로 "예상"되는 상승분
# (INTRA_CONTRACT_COST_TREND_ESTIMATE × 이전계약 term_months)을 먼저 반영한 뒤 갱신
# 성장률을 더함. 이 상수는 generate_monthly_tables.py의 INTRA_CONTRACT_COST_TREND와
# 반드시 같은 값으로 유지해야 함(두 파일이 서로의 상수를 import하진 않으므로 값이 바뀌면
# 양쪽 다 같이 수정 필요 - 이 자체가 두 스크립트를 분리한 데서 오는 구조적 약점).
INTRA_CONTRACT_COST_TREND_ESTIMATE = 0.004  # generate_monthly_tables.py와 동일하게 유지할 것

COST_DRIFT_MEAN = 0.02  # 계약 갱신 시 평균 비용 성장률(2%, 완만한 상승 추세 가정)
# [9/20 조정] 처음 0.15로 뒀더니 연속 계약간 plan 유지율이 53%밖에 안 나옴(비용을
# 이어지게 고친 뒤에도 그대로였음 - 아래 PLAN_RETENTION_PROB을 추가로 넣게 된 계기).
# 0.08로 낮춰서 비용 변동폭 자체도 줄임 (plan 유지율 개선의 주된 원인은 아래
# PLAN_RETENTION_PROB이고, 이건 보조적으로 비용 anchor가 plan 경계값을 덜
# 넘나들게 하는 역할)
COST_DRIFT_SIGMA = 0.08

# [9/20 추가 - 재검토 중 두 번째 결함 발견, 9/20 재수정] 비용을 이어지게 고쳤는데도
# plan 유지율이 여전히 53%였음. 원인: managed_service_plan을 매 계약마다 COST_TO_PLAN_PROBS
# 에서 "확률적으로" 다시 뽑고 있어서, 비용이 거의 그대로여도(예: 1,251,470원->1,241,625원,
# 둘 다 같은 비용구간) plan이 Standard->Basic으로 순전히 샘플링 노이즈 때문에 바뀌었음.
#
# [1차 수정, 폐기] "PLAN_RETENTION_PROB 확률로 무조건 이전 plan 유지"로 고쳤더니 유지율이
# 92.6%까지 올라갔는데, 민혁이 "이게 너무 과한 거 아니냐"고 지적함 - 직접 검증해보니 맞는
# 지적이었음: 연속계약간 비용 변화폭(+30% 이상 성장/-30% 이상 하락)과 plan 변경 여부를
# 교차검증한 결과, 비용이 크게 뛴 쌍(2129쌍 중 +30%초과 2건)이든 거의 안 뛴 쌍(1641쌍)이든
# plan 변경률이 전부 6.5~7.8%로 동일했음 - 즉 이 방식은 "비용이 실제로 얼마나 변했는지"를
# 전혀 반영하지 못하고, 그냥 매 계약마다 동일 확률로 동전을 던지는 것과 다를 게 없었음.
# lock-in(전환비용)이라면 "비용이 크게 바뀌어도 관성 때문에 plan을 잘 안 바꾼다"는 의미여야
# 하는데, 이 방식은 애초에 "비용이 바뀌었는지"조차 보지 않았으므로 lock-in을 구현한 게 아님.
#
# [2차 수정, 최종] 비용이 COST_TO_PLAN_PROBS의 같은 구간(bracket) 안에 머물러 있으면
# plan을 그대로 유지(애초에 바꿀 이유가 없음 - 이전엔 이 경우에도 categorical 샘플링
# 노이즈로 바뀔 수 있었던 게 진짜 버그였음). 비용이 "다른 구간으로 실제로 넘어갔을 때"만
# lock-in 마찰이 작동해서, PLAN_RETENTION_PROB 확률로는 그래도 이전 plan을 유지(전환비용
# 때문에 즉시 반응하지 않음), 나머지 확률로만 새 구간 기준 plan으로 전환.
# 이렇게 하면 "비용 변화가 클수록(=구간을 넘어갈 가능성이 높을수록) plan이 바뀔 확률도
# 높아지는" 인과관계가 실제로 성립함.
PLAN_RETENTION_PROB = 0.6  # 구간이 바뀌었을 때 그래도 이전 plan을 유지할 확률(전환비용/lock-in)


def _weighted_choice(prob_dict):
    keys = list(prob_dict.keys())
    probs = np.array(list(prob_dict.values()), dtype=float)
    probs = probs / probs.sum()
    return keys[rng.choice(len(keys), p=probs)]


def _sample_plan_from_cost(cost_krw):
    for upper, probs in COST_TO_PLAN_PROBS:
        if cost_krw < upper:
            return _weighted_choice(probs)
    return _weighted_choice(COST_TO_PLAN_PROBS[-1][1])


def _cost_bracket_index(cost_krw):
    """비용이 COST_TO_PLAN_PROBS의 몇 번째 구간에 속하는지 인덱스로 반환.
    plan을 다시 뽑을지 말지 판단할 때, 실제로 뽑힌 plan(샘플링 노이즈 포함)이 아니라
    이 "구간" 자체가 바뀌었는지로 판단해야 노이즈와 실제 비용변화를 구분할 수 있음."""
    for i, (upper, _probs) in enumerate(COST_TO_PLAN_PROBS):
        if cost_krw < upper:
            return i
    return len(COST_TO_PLAN_PROBS) - 1


def _sample_latent_vector(prev=None):
    """4개 은닉변수를 뽑는다. prev가 있으면 일부 지속성을 반영."""
    fresh = {
        # [9/20 수정 - 재검토 중 세 번째 결함 발견] "만족도(0~1 스케일)"인데 정규분포를
        # 그대로 쓰면 극단값에서 음수(-0.12)나 1 초과(1.24)가 나옴 - 만족도 점수가
        # 범위를 벗어나는 건 정의상 말이 안 됨(실제로 약 0.8% 레코드에서 발생 확인).
        # np.clip으로 [0,1] 경계에서 잘라냄.
        "customer_satisfaction_latent": float(np.clip(rng.normal(0.5, 0.2), 0.0, 1.0)),
        "budget_cut_prob": rng.beta(2, 8),  # 대부분 낮은 확률, 가끔 높음 (beta는 자체적으로 [0,1] 보장)
        "competitor_pull": rng.beta(2, 8),
        "org_change_flag": rng.binomial(1, 0.08),  # 조직개편 등 이벤트, 드물게 발생
    }
    if prev is None:
        return fresh
    blended = {}
    for k in ["customer_satisfaction_latent", "budget_cut_prob", "competitor_pull"]:
        blended[k] = LATENT_PERSISTENCE * prev[k] + (1 - LATENT_PERSISTENCE) * fresh[k]
    # org_change_flag는 지속성 없이 매 계약마다 새로 판단(이벤트성이라 지속 안 시킴)
    blended["org_change_flag"] = fresh["org_change_flag"]
    return blended


def generate_customer_contract():
    rows = []
    for cust_idx in range(N_CUSTOMERS):
        customer_id = f"CUST_{cust_idx:05d}"

        # --- 고객 레벨 고정 속성 (계약이 바뀌어도 변하지 않음) ---
        industry = _weighted_choice(INDUSTRY_PROBS)
        company_size = _weighted_choice(COMPANY_SIZE_PROBS)

        # 최초 가입일: 최근 5년 내 랜덤 (tenure 산출의 기준점)
        days_ago = rng.integers(30, 5 * 365)
        account_created_date = TODAY - timedelta(days=int(days_ago))

        max_attempts = _weighted_choice(MAX_ATTEMPTS_PROBS)

        contract_start = account_created_date
        prev_latent = None
        prev_cost_anchor = None
        prev_plan = None
        prev_bracket_idx = None
        prev_term_months = None  # 이전 계약의 계약기간(누적 상승분 계산용, 위 주석 참고)

        for seq in range(1, max_attempts + 1):
            contract_id = f"{customer_id}_C{seq}"

            # 계약기간 (규모별 분포)
            term_months = _weighted_choice(TERM_PROBS_BY_SIZE[company_size])
            contract_end = contract_start + pd.DateOffset(months=int(term_months))

            # tenure/renewal_count: "이번 계약이 시작되는 시점" 기준으로 계산
            tenure_months = (contract_start.year - account_created_date.year) * 12 + (
                contract_start.month - account_created_date.month
            )
            renewal_count = seq - 1  # 이전까지 성공적으로 갱신한 횟수

            # 은닉변수를 비용 anchor보다 먼저 뽑음 (9/20 순서 변경 - 아래 참고)
            # (이전 계약 값에서 일부 지속)
            latent = _sample_latent_vector(prev_latent)
            prev_latent = latent

            # 비용 anchor -> 플랜 배정 (기업규모→비용→플랜 순서, 이번에 바로잡은 순서)
            cost_params = COST_LOGNORMAL_PARAMS[company_size]
            if prev_cost_anchor is None:
                # 첫 계약: 로그정규분포에서 새로 뽑음 (mean_krw를 중앙값으로 사용, mu = ln(mean))
                mu = np.log(cost_params["mean_krw"])
                baseline_monthly_cost = float(rng.lognormal(mean=mu, sigma=cost_params["sigma"]))
            else:
                # 2차 계약부터: 이전 계약 비용에서 이어짐(성장률 적용) - 계약마다 리셋 방지
                # [9/20 추가] 이전 계약 동안 2단계(월별 비용)에서 누적됐을 상승분을 먼저 반영
                # (위 INTRA_CONTRACT_COST_TREND_ESTIMATE 관련 주석 참고 - 계약 경계 불연속 방지)
                trend_accum = INTRA_CONTRACT_COST_TREND_ESTIMATE * prev_term_months
                growth = trend_accum + float(rng.normal(COST_DRIFT_MEAN, COST_DRIFT_SIGMA))

                # [9/20 추가 - 3차 재검토] bracket 기반 lock-in 로직을 넣고 재검증했더니
                # plan 유지율이 99.67%까지 치솟음. 원인: COST_DRIFT_SIGMA=0.08 수준의
                # 완만한 성장률로는 비용 구간(10M/50M 경계 - 몇 배수 차이)을 사실상 절대
                # 못 넘어감(교차검증 결과 비용이 +30% 넘게 뛴 쌍조차 2,129쌍 중 2건뿐이었고,
                # 그마저도 plan이 안 바뀜) - 이러면 은닉변수로 만들어둔 budget_cut_prob,
                # org_change_flag가 cost/plan에 전혀 반영이 안 되는 것도 문제였음(그동안 두
                # 변수는 그냥 계약마다 값만 갖고 있을 뿐 아무 데도 쓰이지 않았음).
                # 수정: budget_cut_prob를 "이번 갱신 시점에 실제 예산삭감이 발생했는지"의
                # 확률로 해석해 매 계약마다 베르누이 샘플링하고, 발생 시 비용을 크게(-20~40%
                # 가량) 낮춤. org_change_flag=1(조직개편, 8% 확률)이면 방향은 불확실하니
                # 양방향으로 큰 변동(표준편차 25%)을 추가. 이렇게 하면 (a) 은닉변수가 실제로
                # 관측가능한 비용/플랜 변화에 영향을 주게 되고 (b) plan 변경이 "이유 있는
                # 이벤트"에서만 일어나게 되어 노이즈성 변경(버그)과 근거있는 변경을 구분할 수 있음.
                if rng.random() < latent["budget_cut_prob"]:
                    growth -= abs(float(rng.normal(0.30, 0.10)))  # 예산삭감 충격
                if latent["org_change_flag"] == 1:
                    growth += float(rng.normal(0.0, 0.25))  # 조직개편 충격(양방향)

                baseline_monthly_cost = prev_cost_anchor * np.exp(growth)
            prev_cost_anchor = baseline_monthly_cost

            # plan 배정 (9/20 재수정 - bracket 기반):
            #   1) 이전 계약이 없으면(첫 계약) 비용 기준으로 새로 뽑음
            #   2) 비용이 "이전과 같은 구간"에 머물러 있으면 -> 그대로 이전 plan 유지
            #      (바뀔 이유 자체가 없음. 여기서 categorical 샘플링을 다시 하면 그게
            #      바로 처음 발견했던 버그 - 실제 변화 없이 노이즈로만 plan이 바뀜)
            #   3) 비용이 "다른 구간으로" 실제로 넘어갔으면 -> PLAN_RETENTION_PROB 확률로는
            #      그래도 이전 plan을 유지(전환비용/lock-in으로 즉시 반응하지 않음),
            #      나머지 확률로만 새 구간 기준 plan으로 전환
            bracket_idx = _cost_bracket_index(baseline_monthly_cost)
            if prev_plan is None:
                managed_service_plan = _sample_plan_from_cost(baseline_monthly_cost)
            elif bracket_idx == prev_bracket_idx:
                managed_service_plan = prev_plan
            elif rng.random() < PLAN_RETENTION_PROB:
                managed_service_plan = prev_plan
            else:
                managed_service_plan = _sample_plan_from_cost(baseline_monthly_cost)
            prev_plan = managed_service_plan
            prev_bracket_idx = bracket_idx

            support_plan = _weighted_choice(PLAN_TO_SUPPORT_PROBS[managed_service_plan])

            rows.append(
                {
                    "customer_id": customer_id,
                    "contract_id": contract_id,
                    "contract_seq": seq,
                    "industry": industry,
                    "company_size": company_size,
                    "account_created_date": account_created_date.date().isoformat(),
                    "contract_start_date": contract_start.date().isoformat(),
                    "contract_end_date": contract_end.date().isoformat(),
                    "contract_term_months": term_months,
                    "tenure_months": tenure_months,
                    "renewal_count": renewal_count,
                    "managed_service_plan": managed_service_plan,
                    "support_plan": support_plan,
                    # 아래 두 컬럼은 다음 단계(monthly_cloud_usage) 생성용 내부 연결값.
                    # 최종 15개 Feature/leakage 목록에는 포함되지 않음.
                    "_baseline_monthly_cost_anchor": round(baseline_monthly_cost),
                    # 은닉변수 4종 - 최종 산출물에서는 반드시 제거해야 함(leakage 방지 대상)
                    "_customer_satisfaction_latent": latent["customer_satisfaction_latent"],
                    "_budget_cut_prob": latent["budget_cut_prob"],
                    "_competitor_pull": latent["competitor_pull"],
                    "_org_change_flag": latent["org_change_flag"],
                }
            )

            # 다음 계약은 이번 계약 종료일 바로 다음날부터 시작한다고 가정(공백 없음)
            contract_start = contract_end + timedelta(days=1)
            prev_term_months = term_months  # 다음 반복에서 누적 상승분 계산용

    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = generate_customer_contract()
    df.to_csv("customer_contract_provisional.csv", index=False, encoding="utf-8-sig")

    print(f"총 계약 레코드 수: {len(df)}  (고객 수: {df['customer_id'].nunique()})")
    print("\n[검증 1] company_size 분포 (목표 비중과 비교):")
    print(df.groupby("customer_id").first()["company_size"].value_counts(normalize=True).round(3))
    print("목표:", {k: round(v, 3) for k, v in COMPANY_SIZE_PROBS.items()})

    print("\n[검증 2] 고객당 계약 횟수 분포:")
    print(df.groupby("customer_id").size().value_counts(normalize=True).sort_index().round(3))

    print("\n[검증 3] plan별 평균 baseline 비용 (규모→비용→플랜 인과구조가 맞게 나오는지 확인):")
    print(df.groupby("managed_service_plan")["_baseline_monthly_cost_anchor"].mean().round(0))

    print("\n[검증 4] plan x support_plan 교차표 (상관은 있으되 100% 종속은 아닌지 확인):")
    print(pd.crosstab(df["managed_service_plan"], df["support_plan"], normalize="index").round(2))

    print("\n[검증 5] tenure_months 요약통계:")
    print(df["tenure_months"].describe().round(1))

    print("\n저장 완료: customer_contract_provisional.csv")
