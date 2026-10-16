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




















def test_not_found(client):
    response = client.get("/unknown")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}




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




def test_log_level(monkeypatch, app_module, caplog):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    with TestClient(app_module.create_app()) as client:
        assert client.get("/sleep/bedtime", params={"wake": "07:00"}).status_code == 200
        assert client.get("/sleep/debt", params={"nights": "17"}).status_code == 400
    records = [(record.levelname, record.getMessage()) for record in caplog.records if record.name == "sleep_planner"]
    assert records == [("WARNING", "night_too_long")]




def test_environment_overrides_dotenv(monkeypatch, tmp_path, app_module):
    (tmp_path / ".env").write_text("\n".join(f"{name}=WRONG" for name in TEST_ENV), encoding="utf-8")
    monkeypatch.setattr(app_module, "__file__", str(tmp_path / "app_final.py"))
    with TestClient(app_module.create_app()) as client:
        assert client.get("/").json()["service"] == "Test Sleep Planner"
        assert client.get("/sleep/bedtime", params={"wake": "07:00"}).json()["recommended"] == "23:15"




def test_bedtime_log_is_private(client, caplog):
    caplog.set_level(logging.INFO, logger="sleep_planner")
    assert client.get("/sleep/bedtime", params={"wake": "07:00"}).json()["recommended"] == "23:15"
    messages=[record.getMessage() for record in caplog.records if record.name == "sleep_planner"]
    assert "bedtime_calculated" in messages
    assert not any("07:00" in message for message in messages)
