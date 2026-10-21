"""Контрактные тесты. Импорт приложения выполняется после подстановки всех настроек."""

import importlib
import logging

import pytest
from fastapi.testclient import TestClient


TEST_ENV = {
    "APP_NAME": "Test Sleep Planner",
    "APP_AUTHOR": "Test Author",
    "APP_VERSION": "9.8.7",
    "LOG_LEVEL": "INFO",
    "CYCLE_MINUTES": "90",
    "FALL_ASLEEP_MINUTES": "15",
    "SLEEP_GOAL_HOURS": "7",
    "CAFFEINE_CUTOFF_HOURS": "6",
}
NOTE = "Оценка по средним значениям, не медицинская рекомендация"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name, value in TEST_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def app_module():
    # Не импортировать app_final на уровне модуля тестов: app = create_app()
    # тогда успел бы прочитать личный .env ещё до применения monkeypatch.
    return importlib.import_module("app_final")


@pytest.fixture
def client(monkeypatch, app_module):
    for name, value in TEST_ENV.items():
        monkeypatch.setenv(name, value)
    create_app = app_module.create_app
    with TestClient(create_app()) as c:
        yield c


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version(client):
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {"version": "9.8.7"}


def test_root_and_docs(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {
        "service": "Test Sleep Planner", "author": "Test Author", "docs": "/docs",
    }
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "Test Sleep Planner" in docs.text
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == "9.8.7"
    assert schema["info"]["title"] == "Test Sleep Planner"


def test_bedtime_default(client):
    response = client.get("/sleep/bedtime", params={"wake": "07:00"})
    assert response.status_code == 200
    assert response.json() == {
        "wake": "07:00", "cycle_minutes": 90, "fall_asleep_minutes": 15,
        "options": [
            {"cycles": 6, "bedtime": "21:45", "sleep_hours": 9.0, "below_goal": False},
            {"cycles": 5, "bedtime": "23:15", "sleep_hours": 7.5, "below_goal": False},
            {"cycles": 4, "bedtime": "00:45", "sleep_hours": 6.0, "below_goal": True},
        ],
        "recommended": "23:15", "estimate": True, "note": NOTE,
    }


@pytest.mark.parametrize("params,bedtime,hours,below", [
    ({"wake": "07:00", "cycles": 5}, "23:15", 7.5, False),
    ({"wake": "07:00", "cycles": 5, "cycle_minutes": 100}, "22:25", 8.33, False),
    ({"wake": "07:00", "cycles": 4}, "00:45", 6.0, True),
    ({"wake": "00:30", "cycles": 5}, "16:45", 7.5, False),
    ({"wake": "7:00", "cycles": 5, "latency": 0}, "23:30", 7.5, False),
    ({"wake": "0:05", "cycles": 1, "cycle_minutes": 70, "latency": 60}, "21:55", 1.17, True),
])
def test_bedtime_one_option(client, params, bedtime, hours, below):
    response = client.get("/sleep/bedtime", params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["options"] == [{
        "cycles": params["cycles"], "bedtime": bedtime,
        "sleep_hours": hours, "below_goal": below,
    }]
    assert body["recommended"] == (None if below else bedtime)
    if below:
        assert body["warning"] == "Ни один вариант не даёт 7 часов сна"


@pytest.mark.parametrize("value", ["24:00", "7-00", "7.00", "25:10", "7:60", "abc", "", "07:00\n"])
def test_bad_time(client, value):
    response = client.get("/sleep/bedtime", params={"wake": value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "wake"]


@pytest.mark.parametrize("value", [0, 9, -1, "5.5", "abc"])
def test_bad_cycles(client, value):
    response = client.get("/sleep/bedtime", params={"wake": "07:00", "cycles": value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "cycles"]


@pytest.mark.parametrize("path,anchor", [("bedtime", {"wake": "07:00"}), ("wake-time", {"bedtime": "23:30"})])
@pytest.mark.parametrize("parameter,value", [("cycle_minutes", 69), ("cycle_minutes", 111), ("latency", -1), ("latency", 61)])
def test_cycle_parameter_bounds(client, path, anchor, parameter, value):
    response = client.get(f"/sleep/{path}", params={**anchor, parameter: value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", parameter]


def test_wake_default(client):
    response = client.get("/sleep/wake-time", params={"bedtime": "23:30"})
    assert response.status_code == 200
    assert response.json() == {
        "bedtime": "23:30", "fall_asleep_at": "23:45",
        "options": [
            {"cycles": 4, "wake": "05:45", "sleep_hours": 6.0, "below_goal": True},
            {"cycles": 5, "wake": "07:15", "sleep_hours": 7.5, "below_goal": False},
            {"cycles": 6, "wake": "08:45", "sleep_hours": 9.0, "below_goal": False},
        ],
        "recommended": "07:15", "estimate": True, "note": NOTE,
    }


@pytest.mark.parametrize("params,asleep,wake", [
    ({"bedtime": "22:00", "cycles": 5}, "22:15", "05:45"),
    ({"bedtime": "23:30", "cycles": 5, "latency": 0}, "23:30", "07:00"),
    ({"bedtime": "23:59", "cycles": 5}, "00:14", "07:44"),
])
def test_wake_one_option(client, params, asleep, wake):
    response = client.get("/sleep/wake-time", params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["fall_asleep_at"] == asleep
    assert body["recommended"] == wake
    assert body["options"] == [{"cycles": 5, "wake": wake, "sleep_hours": 7.5, "below_goal": False}]


def test_debt_example(client):
    response = client.get("/sleep/debt", params={"nights": "7.5,6,6.5,5,7,9,9.5", "need": 8})
    assert response.status_code == 200
    assert response.json() == {
        "nights_count": 7, "total_hours": 50.5, "required_hours": 56.0,
        "debt_hours": 5.5, "surplus_hours": 0.0, "average_hours": 7.21,
        "short_nights": 3, "worst_night_hours": 5.0, "estimate": True, "note": NOTE,
    }


@pytest.mark.parametrize("nights,need,expected", [
    ("9,10", "7", (2, 19.0, 14.0, 0.0, 5.0, 9.5, 0, 9.0)),
    ("6,7", "4", (2, 13.0, 8.0, 0.0, 5.0, 6.5, 1, 6.0)),
    ("0", "7", (1, 0.0, 7.0, 7.0, 0.0, 0.0, 1, 0.0)),
    ("16", "12", (1, 16.0, 12.0, 0.0, 4.0, 16.0, 0, 16.0)),
    (",".join(["7"] * 14), "7", (14, 98.0, 98.0, 0.0, 0.0, 7.0, 0, 7.0)),
])
def test_debt_boundaries(client, nights, need, expected):
    response = client.get("/sleep/debt", params={"nights": nights, "need": need})
    assert response.status_code == 200
    body = response.json()
    fields = ("nights_count", "total_hours", "required_hours", "debt_hours", "surplus_hours", "average_hours", "short_nights", "worst_night_hours")
    assert tuple(body[field] for field in fields) == expected


def test_debt_long_night(client):
    response = client.get("/sleep/debt", params={"nights": "7,17"})
    assert response.status_code == 400
    assert response.json() == {"detail": "Ночь не может длиться больше 16 часов"}


@pytest.mark.parametrize("value", ["7;6", "", "семь", ",".join(["7"] * 15), "-1", "7.123", " 7", "7,", "7\n"])
def test_debt_bad_format(client, value):
    response = client.get("/sleep/debt", params={"nights": value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "nights"]


def test_half_up_rounding(client):
    response = client.get("/sleep/debt", params={"nights": "7.00,7.01", "need": "7.0025"})
    assert response.status_code == 200
    body = response.json()
    assert body["average_hours"] == 7.01  # 7.005 -> 7.01, не банковское 7.00.
    assert body["required_hours"] == 14.01  # 14.005 -> 14.01.
    assert body["surplus_hours"] == 0.01


def test_social_example(client):
    response = client.get("/sleep/social-jetlag", params={
        "work_bed": "00:30", "work_wake": "07:00", "free_bed": "02:00", "free_wake": "11:00",
    })
    assert response.status_code == 200
    assert response.json() == {
        "work_midpoint": "03:45", "free_midpoint": "06:30", "jetlag_minutes": 165,
        "jetlag": "2:45", "over_one_hour": True,
        "context": "У большинства людей социальный джетлаг от 1 часа; клинического порога нет",
        "estimate": True, "note": NOTE,
    }


@pytest.mark.parametrize("times,mids,minutes,display,over", [
    (("23:00", "07:00", "23:00", "07:00"), ("03:00", "03:00"), 0, "0:00", False),
    (("23:00", "07:00", "01:00", "09:00"), ("03:00", "05:00"), 120, "2:00", True),
    (("20:00", "04:00", "19:00", "03:00"), ("00:00", "23:00"), 60, "1:00", True),
    (("23:00", "00:01", "23:00", "00:00"), ("23:30", "23:30"), 0, "0:00", False),
    (("23:00", "15:00", "23:59", "15:59"), ("07:00", "07:59"), 59, "0:59", False),
])
def test_social_boundaries(client, times, mids, minutes, display, over):
    params = dict(zip(("work_bed", "work_wake", "free_bed", "free_wake"), times))
    response = client.get("/sleep/social-jetlag", params=params)
    assert response.status_code == 200
    body = response.json()
    assert (body["work_midpoint"], body["free_midpoint"]) == mids
    assert body["jetlag_minutes"] == minutes
    assert body["jetlag"] == display
    assert body["over_one_hour"] is over


@pytest.mark.parametrize("bed,wake", [("07:00", "07:00"), ("00:00", "00:59"), ("00:00", "16:01")])
@pytest.mark.parametrize("prefix", ["work", "free"])
def test_social_invalid_duration(client, bed, wake, prefix):
    params = {"work_bed": "23:00", "work_wake": "07:00", "free_bed": "23:00", "free_wake": "07:00"}
    params.update({f"{prefix}_bed": bed, f"{prefix}_wake": wake})
    response = client.get("/sleep/social-jetlag", params=params)
    assert response.status_code == 400
    assert response.json() == {"detail": "Продолжительность сна должна быть от 1 до 16 часов"}


@pytest.mark.parametrize("params,last,hours", [
    ({"bedtime": "23:15"}, "17:15", 6.0),
    ({"bedtime": "03:00"}, "21:00", 6.0),
    ({"bedtime": "00:00", "hours_before": "3.125"}, "20:52", 3.13),
    ({"bedtime": "0:05", "hours_before": "12"}, "12:05", 12.0),
])
def test_caffeine(client, params, last, hours):
    response = client.get("/sleep/caffeine", params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["last_coffee"] == last
    assert body["hours_before"] == hours
    assert body["basis"] == "Drake et al., 2013: 400 мг кофеина за 6 часов до сна сокращали сон больше чем на час"


def test_shift_example(client):
    response = client.get("/sleep/shift-plan", params={"current": "02:00", "target": "23:30"})
    assert response.status_code == 200
    body = response.json()
    assert body["direction"] == "earlier"
    assert body["total_shift_minutes"] == 150
    assert body["days"] == 10
    assert body["plan"] == [
        {"day": 1, "bedtime": "01:45"}, {"day": 2, "bedtime": "01:30"},
        {"day": 3, "bedtime": "01:15"}, {"day": 4, "bedtime": "01:00"},
        {"day": 5, "bedtime": "00:45"}, {"day": 6, "bedtime": "00:30"},
        {"day": 7, "bedtime": "00:15"}, {"day": 8, "bedtime": "00:00"},
        {"day": 9, "bedtime": "23:45"}, {"day": 10, "bedtime": "23:30"},
    ]
    assert body["context"] == "Шаг сдвига - настройка сервиса, а не медицинская норма"


@pytest.mark.parametrize("current,target,step,direction,minutes,days,first,last", [
    ("23:00", "00:30", 30, "later", 90, 3, "23:30", "00:30"),
    ("02:00", "23:35", 60, "earlier", 145, 3, "01:00", "23:35"),
    ("00:00", "12:00", 60, "earlier", 720, 12, "23:00", "12:00"),
    ("05:00", "00:00", 5, "earlier", 300, 60, "04:55", "00:00"),
    ("23:59", "00:00", 5, "later", 1, 1, "00:00", "00:00"),
])
def test_shift_boundaries(client, current, target, step, direction, minutes, days, first, last):
    response = client.get("/sleep/shift-plan", params={"current": current, "target": target, "step": step})
    assert response.status_code == 200
    body = response.json()
    assert (body["direction"], body["total_shift_minutes"], body["days"]) == (direction, minutes, days)
    assert len(body["plan"]) == days
    assert body["plan"][0] == {"day": 1, "bedtime": first}
    assert body["plan"][-1] == {"day": days, "bedtime": last}


def test_shift_equal(client):
    response = client.get("/sleep/shift-plan", params={"current": "7:00", "target": "07:00"})
    assert response.status_code == 200
    body = response.json()
    assert body["direction"] == "earlier"
    assert body["days"] == 0
    assert body["total_shift_minutes"] == 0
    assert body["plan"] == []
    assert body["message"] == "Режим уже совпадает"


def test_shift_too_long(client):
    response = client.get("/sleep/shift-plan", params={"current": "00:00", "target": "12:00", "step": 5})
    assert response.status_code == 400
    assert response.json() == {"detail": "Слишком долгий переход, увеличьте step"}


@pytest.mark.parametrize("path,field", [
    ("/sleep/bedtime", "wake"), ("/sleep/wake-time", "bedtime"), ("/sleep/debt", "nights"),
    ("/sleep/social-jetlag", "work_bed"), ("/sleep/caffeine", "bedtime"), ("/sleep/shift-plan", "current"),
])
def test_missing_parameter(client, path, field):
    response = client.get(path)
    assert response.status_code == 422
    assert ["query", field] in [item["loc"] for item in response.json()["detail"]]


def test_not_found(client):
    response = client.get("/unknown")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("name,value,path,params,expected", [
    ("CYCLE_MINUTES", "100", "/sleep/bedtime", {"wake": "07:00", "cycles": 5}, {"recommended": "22:25", "cycle_minutes": 100}),
    ("FALL_ASLEEP_MINUTES", "0", "/sleep/wake-time", {"bedtime": "23:30", "cycles": 5}, {"fall_asleep_at": "23:30", "recommended": "07:00"}),
    ("SLEEP_GOAL_HOURS", "10", "/sleep/bedtime", {"wake": "07:00"}, {"recommended": None, "warning": "Ни один вариант не даёт 10 часов сна"}),
    ("SLEEP_GOAL_HOURS", "8", "/sleep/debt", {"nights": "7,8"}, {"required_hours": 16.0, "debt_hours": 1.0, "short_nights": 1}),
    ("CAFFEINE_CUTOFF_HOURS", "8.5", "/sleep/caffeine", {"bedtime": "23:15"}, {"last_coffee": "14:45", "hours_before": 8.5}),
])
def test_settings_change_results(monkeypatch, app_module, name, value, path, params, expected):
    monkeypatch.setenv(name, value)
    with TestClient(app_module.create_app()) as client:
        response = client.get(path, params=params)
    assert response.status_code == 200
    for key, result in expected.items():
        assert response.json()[key] == result


@pytest.mark.parametrize("name,value", [
    ("APP_NAME", ""), ("APP_AUTHOR", " "), ("APP_VERSION", ""),
    ("LOG_LEVEL", "WRONG"), ("LOG_LEVEL", ""),
    ("CYCLE_MINUTES", "200"), ("CYCLE_MINUTES", "69"), ("CYCLE_MINUTES", "90.5"), ("CYCLE_MINUTES", "abc"),
    ("FALL_ASLEEP_MINUTES", "-1"), ("FALL_ASLEEP_MINUTES", "61"), ("FALL_ASLEEP_MINUTES", "1.5"),
    ("SLEEP_GOAL_HOURS", "3.9"), ("SLEEP_GOAL_HOURS", "12.1"), ("SLEEP_GOAL_HOURS", "nan"), ("SLEEP_GOAL_HOURS", "Infinity"), ("SLEEP_GOAL_HOURS", ""),
    ("CAFFEINE_CUTOFF_HOURS", "2.9"), ("CAFFEINE_CUTOFF_HOURS", "12.1"), ("CAFFEINE_CUTOFF_HOURS", "NaN"), ("CAFFEINE_CUTOFF_HOURS", "abc"),
])
def test_invalid_settings(monkeypatch, app_module, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        app_module.create_app()


def test_logs_and_lifespan(monkeypatch, app_module, caplog):
    caplog.set_level(logging.INFO, logger="sleep_planner")
    with TestClient(app_module.create_app()) as client:
        assert client.get("/sleep/bedtime", params={"wake": "07:00"}).json()["recommended"] == "23:15"
        assert client.get("/sleep/wake-time", params={"bedtime": "23:30"}).json()["recommended"] == "07:15"
        assert client.get("/sleep/debt", params={"nights": "7,6"}).json()["debt_hours"] == 1.0
        assert client.get("/sleep/debt", params={"nights": "17"}).status_code == 400
        assert client.get("/sleep/social-jetlag", params={"work_bed": "00:30", "work_wake": "07:00", "free_bed": "02:00", "free_wake": "11:00"}).json()["jetlag_minutes"] == 165
        assert client.get("/sleep/social-jetlag", params={"work_bed": "07:00", "work_wake": "07:00", "free_bed": "02:00", "free_wake": "11:00"}).status_code == 400
        assert client.get("/sleep/caffeine", params={"bedtime": "23:15"}).json()["last_coffee"] == "17:15"
        assert client.get("/sleep/shift-plan", params={"current": "02:00", "target": "23:30"}).json()["days"] == 10
        assert client.get("/sleep/shift-plan", params={"current": "00:00", "target": "12:00", "step": 5}).status_code == 400
    records = [(record.levelname, record.getMessage()) for record in caplog.records if record.name == "sleep_planner"]
    assert records == [
        ("INFO", "service_started version=9.8.7"), ("INFO", "bedtime_calculated"),
        ("INFO", "wake_time_calculated"), ("INFO", "debt_calculated nights=2"),
        ("WARNING", "night_too_long"), ("INFO", "social_jetlag_calculated"),
        ("WARNING", "invalid_sleep_duration"), ("INFO", "caffeine_calculated"),
        ("INFO", "shift_plan_calculated days=10"), ("WARNING", "shift_too_long"),
        ("INFO", "service_stopped"),
    ]
    messages = "\n".join(message for _, message in records)
    assert "07:00" not in messages
    assert "23:30" not in messages
    assert "00:30" not in messages
    assert "23:15" not in messages
    assert "nights=7,6" not in messages


def test_log_level(monkeypatch, app_module, caplog):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    with TestClient(app_module.create_app()) as client:
        assert client.get("/sleep/bedtime", params={"wake": "07:00"}).status_code == 200
        assert client.get("/sleep/debt", params={"nights": "17"}).status_code == 400
    records = [(record.levelname, record.getMessage()) for record in caplog.records if record.name == "sleep_planner"]
    assert records == [("WARNING", "night_too_long")]


@pytest.mark.parametrize("path,params,field", [
    ("/sleep/debt", {"nights": "7", "need": "3.9"}, "need"),
    ("/sleep/debt", {"nights": "7", "need": "12.1"}, "need"),
    ("/sleep/debt", {"nights": "7", "need": "NaN"}, "need"),
    ("/sleep/debt", {"nights": "7", "need": "Infinity"}, "need"),
    ("/sleep/caffeine", {"bedtime": "23:15", "hours_before": "2.9"}, "hours_before"),
    ("/sleep/caffeine", {"bedtime": "23:15", "hours_before": "12.1"}, "hours_before"),
    ("/sleep/caffeine", {"bedtime": "23:15", "hours_before": "nan"}, "hours_before"),
    ("/sleep/shift-plan", {"current": "02:00", "target": "23:30", "step": 4}, "step"),
    ("/sleep/shift-plan", {"current": "02:00", "target": "23:30", "step": 61}, "step"),
])
def test_other_parameter_bounds(client, path, params, field):
    response = client.get(path, params=params)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", field]


def test_environment_overrides_dotenv(monkeypatch, tmp_path, app_module):
    (tmp_path / ".env").write_text("\n".join(f"{name}=WRONG" for name in TEST_ENV), encoding="utf-8")
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app_final.py"))
    with TestClient(app_module.create_app()) as client:
        assert client.get("/").json()["service"] == "Test Sleep Planner"
        assert client.get("/sleep/bedtime", params={"wake": "07:00"}).json()["recommended"] == "23:15"


@pytest.mark.parametrize("path,params,key,value", [
    ("/sleep/bedtime", {"wake": "07:00"}, "recommended", "23:15"),
    ("/sleep/wake-time", {"bedtime": "23:30"}, "recommended", "07:15"),
    ("/sleep/debt", {"nights": "7"}, "debt_hours", 0.0),
    ("/sleep/social-jetlag", {"work_bed": "23:00", "work_wake": "07:00", "free_bed": "23:00", "free_wake": "07:00"}, "jetlag_minutes", 0),
    ("/sleep/caffeine", {"bedtime": "23:15"}, "last_coffee", "17:15"),
    ("/sleep/shift-plan", {"current": "02:00", "target": "23:30"}, "days", 10),
])
def test_estimate_notice(client, path, params, key, value):
    response = client.get(path, params=params)
    assert response.status_code == 200
    assert response.json()[key] == value
    assert response.json()["estimate"] is True
    assert response.json()["note"] == NOTE
