# -*- coding: utf-8 -*-
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_batch.korean import native, read_number, sino  # noqa: E402
from tts_batch.normalize import expand_numbers, fix_josa, normalize  # noqa: E402


@pytest.mark.parametrize("n,expected", [
    (0, "영"),
    (1, "일"),
    (8, "팔"),
    (10, "십"),          # not 일십
    (11, "십일"),
    (20, "이십"),
    (62, "육십이"),
    (95, "구십오"),
    (100, "백"),         # not 일백
    (111, "백십일"),
    (239, "이백삼십구"),
    (857, "팔백오십칠"),
    (1000, "천"),        # not 일천
    (3928, "삼천구백이십팔"),
    (10000, "만"),       # not 일만
    (21544, "이만 천오백사십사"),
    (43636, "사만 삼천육백삼십육"),
    (99999, "구만 구천구백구십구"),
])
def test_sino(n, expected):
    assert sino(n) == expected


@pytest.mark.parametrize("n,expected", [
    (1, "한"),
    (2, "두"),
    (3, "세"),
    (4, "네"),
    (8, "여덟"),
    (10, "열"),
    (15, "열다섯"),
    (20, "스무"),        # not 스물
    (21, "스물한"),
    (22, "스물두"),
    (23, "스물세"),
    (62, "예순두"),
    (75, "일흔다섯"),
    (99, "아흔아홉"),
])
def test_native(n, expected):
    assert native(n) == expected


def test_native_out_of_range_raises():
    with pytest.raises(ValueError):
        native(100)
    with pytest.raises(ValueError):
        native(0)


def test_native_falls_back_to_sino_above_99():
    # 295발 -> "이백구십오 발"; native numerals stop being idiomatic here.
    assert read_number(295, "native") == "이백구십오"
    assert read_number(99, "native") == "아흔아홉"


@pytest.mark.parametrize("text,expected", [
    # Sino counters
    ("보스의 체력은 80퍼센트입니다.", "보스의 체력은 팔십 퍼센트입니다."),
    ("감지 범위는 239미터입니다.", "감지 범위는 이백삼십구 미터입니다."),
    ("목적지까지 남은 거리는 12킬로미터입니다.", "목적지까지 남은 거리는 십이 킬로미터입니다."),
    ("남은 시간은 5초입니다.", "남은 시간은 오 초입니다."),
    # Clock time is Sino by project decision: 23시 -> 이십삼 시
    ("현재 시각은 23시 50분입니다.", "현재 시각은 이십삼 시 오십 분입니다."),
    # Dates attach directly
    ("7월 10일 작전 기록을 저장했습니다.", "칠월 십일 작전 기록을 저장했습니다."),
    ("12월 25일 작전 기록을 저장했습니다.", "십이월 이십오일 작전 기록을 저장했습니다."),
    # A span of days must stay separated: "오일" run together is the word for oil
    ("가을 수확제 이벤트 종료까지 5일 남았습니다.",
     "가을 수확제 이벤트 종료까지 오 일 남았습니다."),
    ("이벤트 종료까지 29일 남았습니다.", "이벤트 종료까지 이십구 일 남았습니다."),
    # Native counters
    ("위상석을 8개 판매했습니다.", "위상석을 여덟 개 판매했습니다."),
    ("수호자의 수는 15명입니다.", "수호자의 수는 열다섯 명입니다."),
    ("던전의 남은 탐색 구역은 5곳입니다.", "던전의 남은 탐색 구역은 다섯 곳입니다."),
    ("쌍검의 탄약은 62발 남았습니다.", "쌍검의 탄약은 예순두 발 남았습니다."),
    ("던전 내부의 위상력이 외부보다 5배 높습니다.", "던전 내부의 위상력이 외부보다 다섯 배 높습니다."),
    # Duration stays native
    ("전술 데이터의 효과 지속 시간은 1시간입니다.", "전술 데이터의 효과 지속 시간은 한 시간입니다."),
    # Native counter over 99 falls back to Sino
    ("저격총의 장탄 수는 295발입니다.", "저격총의 장탄 수는 이백구십오 발입니다."),
    # Bare number
    ("장비의 공격력이 562 증가했습니다.", "장비의 공격력이 오백육십이 증가했습니다."),
    ("전술 분석의 위상력 소모량은 111입니다.", "전술 분석의 위상력 소모량은 백십일입니다."),
])
def test_expand_numbers(text, expected):
    assert expand_numbers(text) == expected


@pytest.mark.parametrize("text,expected", [
    # Errors already present in the source script
    ("정찰 작전 중 예상하지 못한 정찰병가 발견되었습니다.",
     "정찰 작전 중 예상하지 못한 정찰병이 발견되었습니다."),
    ("희귀 장비을 사용할까요?", "희귀 장비를 사용할까요?"),
    ("차원종를 제거했습니다.", "차원종을 제거했습니다."),
    ("전술 데이터은 현재 사용 조건을 충족하지 못했습니다.",
     "전술 데이터는 현재 사용 조건을 충족하지 못했습니다."),
    # ㄹ-final takes 로, so these must NOT change
    ("위상검으로 공격합니다.", "위상검으로 공격합니다."),
    ("연속으로 공격합니다.", "연속으로 공격합니다."),
    ("최대치로 상승했습니다.", "최대치로 상승했습니다."),
    # The noun 효과 must survive: 과/와 is deliberately not corrected
    ("고급 장비의 효과 지속 시간은 2분입니다.", "고급 장비의 효과 지속 시간은 2분입니다."),
    # 가을 is the season, not 가 + 을 -- 20 lines in this script say 가을 수확제
    ("가을 수확제 한정 아이템을 획득했습니다.", "가을 수확제 한정 아이템을 획득했습니다."),
    ("가을 수확제 이벤트 종료까지 5일 남았습니다.", "가을 수확제 이벤트 종료까지 5일 남았습니다."),
])
def test_fix_josa(text, expected):
    assert fix_josa(text) == expected


@pytest.mark.parametrize("token", ["가을", "마을", "아이", "사이", "증가", "국가"])
def test_protected_nouns_survive(token):
    assert fix_josa("%s 확인" % token) == "%s 확인" % token


@pytest.mark.parametrize("text,expected", [
    # Particle depends on how the number is *spoken*: 팔 is ㄹ-final -> 을
    ("경험치 3928를 획득했습니다.", "경험치 삼천구백이십팔을 획득했습니다."),
    # 육 is consonant-final -> 을 (source was already right)
    ("골드 43636을 획득했습니다.", "골드 사만 삼천육백삼십육을 획득했습니다."),
    # 팔 is consonant-final -> 이
    ("강화 단계가 8이 되었습니다.", "강화 단계가 팔이 되었습니다."),
    # Number expansion splits the token, then 분로 -> 분으로
    ("충격파의 재사용 대기 시간이 30분로 변경되었습니다.",
     "충격파의 재사용 대기 시간이 삼십 분으로 변경되었습니다."),
    ("연속 사격의 재사용 대기 시간이 1시간로 변경되었습니다.",
     "연속 사격의 재사용 대기 시간이 한 시간으로 변경되었습니다."),
    ("위상력 회복에 7분가 필요합니다.", "위상력 회복에 칠 분이 필요합니다."),
    # 초 has no batchim, so 로 is already correct
    ("위상 폭발의 재사용 대기 시간이 30초로 변경되었습니다.",
     "위상 폭발의 재사용 대기 시간이 삼십 초로 변경되었습니다."),
])
def test_normalize_end_to_end(text, expected):
    assert normalize(text) == expected
