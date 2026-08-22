# Git 브랜치 사용법 — 처음부터 끝까지

혼자 책으로 공부하거나 문제 풀 때, 챕터(또는 주제) 단위로 브랜치를 나눠서 작업하는 방법을 정리한 문서. 나중에 까먹으면 이 문서만 보고 그대로 따라 하면 됨.

---

## 0. 먼저 알아야 할 것 — 폴더는 하나만, 브랜치만 바꿔가며 씀

VS Code에서 프로젝트 폴더(예: `python-book`)를 **한 번만 열어두고, 계속 그 폴더만 사용**한다. 챕터가 바뀔 때마다 하는 건 "새 폴더 열기"가 아니라 **"새 브랜치로 전환(checkout)하기"**다.

> 폴더는 "책상", 브랜치는 "그 책상 위에 지금 펼쳐놓은 노트"라고 생각하면 됨. 책상은 하나만 있으면 되고, 어떤 노트(브랜치)를 펼쳐놓느냐만 계속 바뀌는 것.

---

## 1. 챕터(기능) 시작할 때 — 브랜치 만들기

```bash
# 1) main으로 이동
git checkout main

# 2) 원격의 최신 상태를 받아오기 (다른 컴퓨터에서 작업했을 수도 있으니)
git pull origin main

# 3) main에서 새 브랜치 만들면서 바로 그 브랜치로 이동
git checkout -b feature/ch01-intro
```

`-b` 옵션은 "만들면서 동시에 그 브랜치로 갈아탄다"는 뜻. `feature/ch01-intro`처럼 이름을 지으면 나중에 봤을 때 뭘 하던 브랜치인지 바로 알 수 있음.

### 브랜치 이름 짓는 규칙 (통일해서 쓰기)
```
feature/ch01-intro       (챕터 단위 작업일 때)
feature/0821-mysql-join    (날짜 단위 작업일 때)
feature/leetcode-42          (문제 번호 단위 작업일 때)
```

---

## 2. 브랜치 위에서 작업하기 — 마음껏 커밋

```bash
# 작업하고 나서
git add .
git commit -m "1장 실습 코드 따라치기"

# 계속 작업하면서 여러 번 커밋해도 됨
git commit -m "1장 연습문제 1~5번 풀이"
```

커밋 메시지 앞에 `[ch01]`처럼 태그를 붙이면 나중에 로그만 봐도 어느 챕터 작업인지 바로 보임:
```
[ch01] 변수와 자료형 실습
[ch01] 연습문제 1~5번 풀이
```

### 원격에 올려두기 (백업 겸, 다른 컴퓨터에서 이어가려면 필수)
```bash
git push origin feature/ch01-intro
```

---

## 3. 지금 어느 상태인지 확인하는 법

### 지금 어느 브랜치에 있는지
```bash
git branch
```
현재 브랜치 앞에 `*` 표시됨.

### 전체 브랜치 구조를 그림으로 보기 (제일 유용함)
```bash
git log --oneline --graph --all
```
브랜치들이 어디서 갈라졌는지, 어디서 합쳐졌는지 한눈에 보임.

### 이 브랜치가 main에서 정확히 어디서 갈라졌는지 확인
```bash
git merge-base main 브랜치이름
```

### 로컬 브랜치가 원격 어떤 브랜치를 추적하고 있는지
```bash
git branch -vv
```

---

## 4. 챕터 다 끝나면 — main으로 합치기 (merge)

```bash
# 1) main으로 이동
git checkout main

# 2) 방금 작업한 브랜치를 main에 합치기
git merge feature/ch01-intro

# 3) 합친 결과를 원격에 올리기
git push origin main
```

### merge 결과가 두 가지로 나뉨 (둘 다 정상)

| 상황 | 결과 |
|---|---|
| `main`이 그동안 전혀 안 바뀐 상태에서 merge | **Fast-forward** — 그냥 포인터만 이동, 별도 커밋 안 생김 |
| `main`이 이미 다른 브랜치로 변경된 상태에서 merge | **Merge 커밋**이 새로 생김 (`Merge branch 'feature/...'`) |

둘 다 정상적인 병합 결과이니 걱정할 필요 없음.

---

## 5. 다 쓴 브랜치 정리하기

```bash
# 로컬 브랜치 삭제
git branch -d feature/ch01-intro

# 원격 브랜치도 삭제
git push origin --delete feature/ch01-intro
```

---

## 6. 다음 챕터 시작하기 — 1~5번 반복

```bash
git checkout main
git pull origin main
git checkout -b feature/ch02-variables

# ... 작업, 커밋 ...

git checkout main
git merge feature/ch02-variables
git push origin main
```

---

## 7. 충돌(conflict)이 났을 때 (참고, 자주는 안 생김)

같은 파일 같은 부분을 서로 다른 브랜치에서 건드렸을 때 발생.

```
<<<<<<< HEAD
지금 브랜치의 내용
=======
합치려는 브랜치의 내용
>>>>>>> feature/ch02-variables
```

이 표시가 파일 안에 생기면:
1. 파일을 직접 열어서 어떤 내용을 남길지 손으로 정리
2. `<<<<<<<`, `=======`, `>>>>>>>` 기호를 전부 지움
3. 다시 커밋
```bash
git add .
git commit -m "충돌 해결"
```

---

## 8. 전체 흐름 한눈에 요약

```
main
 │
 ├─ checkout -b feature/ch01-intro   ← 브랜치 시작
 │    (작업, 커밋, push)
 │
 ├─ checkout main → merge feature/ch01-intro → push   ← 병합
 │    branch -d feature/ch01-intro                        ← 정리
 │
 ├─ checkout -b feature/ch02-variables   ← 다음 챕터, 반복
 │    ...
```

---

## 자주 쓰는 명령어 모음 (치트시트)

| 상황 | 명령어 |
|---|---|
| 새 브랜치 만들면서 이동 | `git checkout -b 브랜치이름` |
| 브랜치 전환 | `git checkout 브랜치이름` |
| 현재 브랜치 확인 | `git branch` |
| 전체 브랜치 그래프 보기 | `git log --oneline --graph --all` |
| 브랜치가 어디서 갈라졌는지 | `git merge-base main 브랜치이름` |
| 커밋 | `git add .` → `git commit -m "메시지"` |
| 원격에 올리기 | `git push origin 브랜치이름` |
| 병합 | `git checkout main` → `git merge 브랜치이름` → `git push origin main` |
| 브랜치 삭제(로컬) | `git branch -d 브랜치이름` |
| 브랜치 삭제(원격) | `git push origin --delete 브랜치이름` |