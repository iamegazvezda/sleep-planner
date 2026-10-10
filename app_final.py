"""Sleep Planner: расчёты по упрощённой модели сна, без хранения пользовательских данных."""
from decimal import Decimal, ROUND_HALF_UP
from fastapi import FastAPI, HTTPException, Query

TIME_PATTERN = r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$"
NIGHTS_PATTERN = r"^\d{1,2}(\.\d{1,2})?(,\d{1,2}(\.\d{1,2})?){0,13}$"
DAY_MINUTES = 1440
NOTE = "Оценка по средним значениям, не медицинская рекомендация"

def to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def clock(value: int) -> str:
    value %= DAY_MINUTES
    return f"{value // 60:02d}:{value % 60:02d}"


def rounded_hours(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def estimate(**fields) -> dict:
    return {**fields, "estimate": True, "note": NOTE}


def cycle_options(
    anchor: int, cycles: int | None, cycle_minutes: int, latency: int,
    goal: Decimal, *, backwards: bool,
) -> list[dict]:
    choices = [cycles] if cycles is not None else ([6, 5, 4] if backwards else [4, 5, 6])
    options = []
    for count in choices:
        sleep_minutes = count * cycle_minutes
        offset = sleep_minutes + latency
        options.append({
            "cycles": count,
            "bedtime" if backwards else "wake": clock(
                anchor - offset if backwards else anchor + offset
            ),
            "sleep_hours": rounded_hours(Decimal(sleep_minutes) / 60),
            "below_goal": Decimal(sleep_minutes) < goal * 60,
        })
    return options


def recommendation(options: list[dict], key: str, goal: Decimal) -> dict:
    suitable = [item for item in options if not item["below_goal"]]
    if suitable:
        selected = min(suitable, key=lambda item: item["cycles"])
        return {"recommended": selected[key]}
    return {
        "recommended": None,
        "warning": f"Ни один вариант не даёт {format(goal.normalize(), 'f')} часов сна",
    }


def create_app() -> FastAPI:
    settings = {
        "APP_NAME": "Sleep Planner", "APP_AUTHOR": "Alex Kryuchatov",
        "APP_VERSION": "1.0.0", "CYCLE_MINUTES": 90,
        "FALL_ASLEEP_MINUTES": 15, "SLEEP_GOAL_HOURS": Decimal("7"),
    }
    application = FastAPI(title=settings["APP_NAME"], version=settings["APP_VERSION"])

    @application.get("/")
    def root():
        return {
            "service": settings["APP_NAME"],
            "author": settings["APP_AUTHOR"],
            "docs": "/docs",
        }


    @application.get("/health")
    def health():
        return {"status": "ok"}


    @application.get("/version")
    def version():
        return {"version": settings["APP_VERSION"]}


    @application.get("/sleep/bedtime")
    def bedtime(
        wake: str = Query(..., pattern=TIME_PATTERN),
        cycles: int | None = Query(None, ge=1, le=8),
        cycle_minutes: int = Query(settings["CYCLE_MINUTES"], ge=70, le=110),
        latency: int = Query(settings["FALL_ASLEEP_MINUTES"], ge=0, le=60),
    ):
        options = cycle_options(
            to_minutes(wake), cycles, cycle_minutes, latency,
            settings["SLEEP_GOAL_HOURS"], backwards=True,
        )
        result = estimate(
            wake=clock(to_minutes(wake)), cycle_minutes=cycle_minutes,
            fall_asleep_minutes=latency, options=options,
            **recommendation(options, "bedtime", settings["SLEEP_GOAL_HOURS"]),
        )
        return result


    @application.get("/sleep/wake-time")
    def wake_time(
        bedtime: str = Query(..., pattern=TIME_PATTERN),
        cycles: int | None = Query(None, ge=1, le=8),
        cycle_minutes: int = Query(settings["CYCLE_MINUTES"], ge=70, le=110),
        latency: int = Query(settings["FALL_ASLEEP_MINUTES"], ge=0, le=60),
    ):
        bed = to_minutes(bedtime)
        options = cycle_options(
            bed, cycles, cycle_minutes, latency,
            settings["SLEEP_GOAL_HOURS"], backwards=False,
        )
        result = estimate(
            bedtime=clock(bed), fall_asleep_at=clock(bed + latency), options=options,
            **recommendation(options, "wake", settings["SLEEP_GOAL_HOURS"]),
        )
        return result


    return application


app = create_app()
