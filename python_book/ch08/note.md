# 8장. 클래스

## 객체

여러 가지 속성을 가질 수 있는 대상. 객체와 관련된 코드를 분리할 수 있게 하는 것이 객체 지향 프로그래밍의 핵심임. 근데 이런 코드가 너무 자주 사용되니까, 클래스라는 구조를 만들게 됨.

---

## 클래스 선언하기

클래스는 객체를 효율적으로 생성하기 위해 만들어진 구문. 참고로 클래스 이름은 카멜 케이스(파스칼 케이스)로 만들어줘야 함.

```python
인스턴스이름(변수이름) = 클래스이름()   # 생성자 함수라고 함
```

클래스 기반으로 만들어진 객체를 **인스턴스**라고 함.

---

## 생성자

클래스 이름과 같은 함수를 생성자라고 함.

클래스 내부에 `__init__`이라는 함수를 만들면 객체를 생성할 때 처리할 내용을 작성 가능.

```python
class 클래스이름:
    def __init__(self, 추가적인_매개변수):
        pass
```

클래스 내부 함수(method)는 첫 번째 매개변수를 반드시 `self`로 받아야 함. self는 자기 자신을 나타내는 (딕셔너리처럼 접근 가능한) 인스턴스 그 자체.

self가 가진 속성과 기능에 접근할 때는 `self.<식별자>`로 해야 함.
```python
self.name = name
```

**클래스 인스턴스의 속성에 접근하는 방법**
```python
인스턴스이름.인스턴스속성
```

**소멸자**: 생성자의 반대. `__del__`. 인스턴스가 소멸될 때 호출되는 함수. 소멸자가 호출되는 시점이 약간 복잡함 (뒤에서 설명 예정).

---

## method

클래스가 가지고 있는 함수. 생성자 선언하는 것과 형태가 똑같음.

---

## 클래스의 부가적인 기능들

### 어떤 클래스의 인스턴스인지 확인하기 — `isinstance()`

객체가 어떤 클래스로부터 만들어졌는지 확인하는 함수.
```python
isinstance(인스턴스, 클래스)
```

단순히 인스턴스 확인이라면 `type(인스턴스) == 클래스`로 써도 됨. 대신 **상속을 사용할 땐 다르게 동작함.**

예를 들어 `Human` 클래스를 상속받아 만들어진 `Student` 클래스가 있고, 그 클래스에서 만들어진 객체가 `student`일 때:

```python
isinstance(student, Human)   # True
type(student) == Human        # False
```

> `isinstance()`는 **상속 관계까지 고려**해서 확인해주는 반면, `type() ==`는 **정확히 그 클래스인지만** 확인함. `student`는 `Human`을 상속받은 `Student`의 인스턴스라서, `isinstance`는 "Human 계열이 맞다"고 True를 주지만, `type()`은 "정확히 Human 그 자체는 아니다"라고 False를 줌.

---

### 클래스의 변수와 메소드

인스턴스의 속성과 메소드 말고, 클래스 자체의 변수와 메소드도 있음.

**클래스 변수**
```python
class 클래스이름:
    클래스변수 = 값
```

> ✅ **해결된 궁금증 — 클래스 변수를 왜 init 밖에 만드나? "클래스에서 쓸 변수는 init 안에 넣어라"는 원칙이랑 다르지 않나?**
> 원칙 자체는 여전히 맞음 — "인스턴스마다 달라야 하는 데이터"는 `__init__` 안에서 `self.변수`로 만들어야 함. 클래스 변수는 그 원칙의 **예외**로, **"모든 인스턴스가 공통으로 공유해야 하는 값"일 때만** 예외적으로 사용함.
> ```python
> class Person:
>     name = "person name"   # 클래스 변수 (모두가 공유)
>     age = 18
>     def __init__(self, name):
>         self.name = name    # 인스턴스 변수 (각자 따로)
>
> p1 = Person("인스턴트1")
> p1.age = 20
> print(Person.age)   # 18  ← 클래스 자체 값은 안 바뀜!
> print(p1.age)         # 20  ← 인스턴스 값만 바뀜
> ```
> 강사님이 "init 안에 넣으라"고 한 건 대부분의 경우(인스턴스마다 다른 데이터)에 해당하는 원칙이고, 클래스 변수는 그 원칙이 적용 안 되는 특수한 경우.

**클래스 변수 접근**
```python
클래스이름.변수이름
```

**클래스 함수** — `@classmethod` 데코레이터를 사용하여 만듦.

```python
class 클래스이름:
    @classmethod
    def 클래스함수(cls, 매개변수):
        pass
```

**클래스 함수 호출**
```python
클래스이름.함수이름(매개변수)
```

> ✅ **해결된 궁금증 — 그냥 인스턴스 함수를 만드는 게 낫지 않나? @classmethod는 왜 필요한가**
> 인스턴스 메소드는 `student = Student(...)`처럼 **먼저 인스턴스를 만든 다음에만** 부를 수 있음. 근데 "인스턴스가 아직 없는 시점에도" 클래스 차원에서 처리해야 하는 상황이 있는데, 그럴 때 `@classmethod`가 필요함.
>
> 대표적인 활용 — **대체 생성자**:
> ```python
> class Student:
>     def __init__(self, name, age):
>         self.name = name
>         self.age = age
>
>     @classmethod
>     def getobject(cls, name='Steve', age=25):
>         return cls(name, age)   # cls(...)는 결국 Student(...)와 같음
>
> std = Student.getobject()   # 인스턴스 없이, 클래스 이름으로 바로 호출!
> std.name, std.age             # ('Steve', 25)
> ```
> 핵심 차이: 인스턴스 메소드는 인스턴스가 먼저 있어야 부를 수 있지만, `@classmethod`는 **인스턴스를 만들기 전에도** `Student.getobject()`처럼 클래스 자체로 바로 호출 가능함.

---

## 부가적인 것들

### 1. 프라이빗 변수

객체를 효율적으로 사용하기 위한 기능. 클래스 내부의 변수를 외부에서 사용 못 하게 하기 위해서 사용: `__변수이름` 형태.

### 2. 게터/세터

클래스 외부에서 프라이빗 변수에 접근 불가하므로 간접적으로 접근하는 방법. 클래스 안에 함수를 하나 만들고 그 함수를 통해 프라이빗 변수에 접근한 다음, 외부에서 그 함수를 호출하면 간접적으로 접근 가능함.

**옛날 방식 — 함수 이름을 따로 지어서 (매번 괄호 붙여야 함)**
```python
class Student:
    def __init__(self, name):
        self.__name = name    # private 변수

    def get_name(self):    # 조회용 함수
        return self.__name

    def set_name(self, new_name):    # 수정용 함수
        self.__name = new_name

s = Student("Steve")
s.get_name()          # 조회할 때 함수처럼 괄호 붙여서 호출
s.set_name("Bill")     # 수정할 때도 함수 호출
```

> ✅ **해결된 궁금증 — `@property`와 `@<게터함수이름>.setter`로 어떻게 더 쉽게 만드는가**
>
> ```python
> class Student:
>     def __init__(self, name):
>         self.__name = name
>
>     @property
>     def name(self):        # 조회(get)
>         return self.__name
>
>     @name.setter
>     def name(self, new_name):    # 수정(set)
>         self.__name = new_name
>
> s = Student("Steve")
> s.name              # 조회 — 변수처럼! 괄호 없음
> s.name = "Bill"       # 수정 — 변수에 값 대입하듯이!
> ```
>
> **핵심 장점 — 겉모습은 변수처럼, 속은 함수처럼**: `@property`를 쓰면 사용하는 사람은 `s.name`을 그냥 평범한 변수처럼 쓸 수 있음. 근데 실제로는 그 뒤에서 함수(검증, 로그 등)가 몰래 실행됨.
> ```python
> @property
> def name(self):
>     return self.__name
>
> @name.setter
> def name(self, new_name):
>     if new_name == "":
>         raise ValueError("이름은 빈 문자열일 수 없음")   # 검증 로직 추가 가능!
>     self.__name = new_name
> ```
> 만약 처음부터 그냥 public 변수(`self.name`)로 만들었다가, 나중에 "이름이 빈 값이면 안 된다"는 검증을 추가하고 싶어지면, `@property`로 바꿔도 **기존에 `s.name`, `s.name = "Bill"`이라고 쓰던 모든 코드는 한 글자도 안 고쳐도 됨.** 겉으로는 여전히 변수처럼 보이니까.

### 3. 상속

🔍 **미해결 — 나중에 2회독하면서 강의자료 같이 보기로 함**

---

> 🔍 **전체 노트**: 클래스는 전체적으로 이해 못 하는 게 많음. 2회독, 3회독할 때 강의자료 참고해서 같이 볼 것.