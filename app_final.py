"""Sleep Planner: расчёты по упрощённой модели сна, без хранения пользовательских данных."""

from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query


TIME_PATTERN = r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$"
NIGHTS_PATTERN = r"^\d{1,2}(\.\d{1,2})?(,\d{1,2}(\.\d{1,2})?){0,13}$"
DAY_MINUTES = 1440
NOTE = "Оценка по средним значениям, не медицинская рекомендация"
SOCIAL_CONTEXT = (
    "У большинства людей социальный джетлаг от 1 часа; клинического порога нет"
)
SHIFT_CONTEXT = "Шаг сдвига - настройка сервиса, а не медицинская норма"
CAFFEINE_BASIS = (
    "Drake et al., 2013: 400 мг кофеина за 6 часов до сна сокращали сон больше чем на час"
)
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
logger = logging.getLogger("sleep_planner")


def read_text(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise ValueError(f"{name}: требуется непустая строка")
    return value


def read_integer(name: str, default: str, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError:
        raise ValueError(
            f"{name}: требуется целое число от {minimum} до {maximum}"
        ) from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}: требуется целое число от {minimum} до {maximum}")
    return value


def read_decimal(name: str, default: str, minimum: int, maximum: int) -> Decimal:
    try:
        value = Decimal(os.getenv(name, default))
    except InvalidOperation:
        raise ValueError(
            f"{name}: требуется конечное число от {minimum} до {maximum}"
        ) from None
    if not value.is_finite() or not minimum <= value <= maximum:
        raise ValueError(f"{name}: требуется конечное число от {minimum} до {maximum}")
    return value


def read_settings() -> dict:
    """Загрузка до чтения; экспортированные переменные имеют приоритет."""
    load_dotenv(Path(__file__).with_name(".env"))
    settings = {
        "APP_NAME": read_text("APP_NAME", "Sleep Planner"),
        "APP_AUTHOR": read_text("APP_AUTHOR", "Alex Kryuchatov"),
        "APP_VERSION": read_text("APP_VERSION", "1.0.0"),
        "LOG_LEVEL": read_text("LOG_LEVEL", "INFO").upper(),
        "CYCLE_MINUTES": read_integer("CYCLE_MINUTES", "90", 70, 110),
        "FALL_ASLEEP_MINUTES": read_integer("FALL_ASLEEP_MINUTES", "15", 0, 60),
        "SLEEP_GOAL_HOURS": read_decimal("SLEEP_GOAL_HOURS", "7", 4, 12),
        "CAFFEINE_CUTOFF_HOURS": read_decimal("CAFFEINE_CUTOFF_HOURS", "6", 3, 12),
    }
    if settings["LOG_LEVEL"] not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("LOG_LEVEL: допустимы DEBUG, INFO, WARNING, ERROR, CRITICAL")
    return settings


def configure_logging(level: str) -> None:
    """Один собственный stderr-handler, даже при повторных create_app в тестах."""
    logger.setLevel(level)
    if not any(handler.name == "sleep_planner_console" for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.set_name("sleep_planner_console")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)


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


def reject(event: str, detail: str) -> None:
    # Только фиксированное имя события; пользовательские значения не логируются.
    logger.warning(event)
    raise HTTPException(status_code=400, detail=detail)


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
    settings = read_settings()
    configure_logging(settings["LOG_LEVEL"])

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logger.info("service_started version=%s", settings["APP_VERSION"])
        try:
            yield
        finally:
            logger.info("service_stopped")

    application = FastAPI(
        title=settings["APP_NAME"], version=settings["APP_VERSION"],
        description=NOTE, lifespan=lifespan,
    )

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
        logger.info("bedtime_calculated")
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
        logger.info("wake_time_calculated")
        return result

    @application.get("/sleep/debt")
    def debt(
        nights: str = Query(..., pattern=NIGHTS_PATTERN),
        need: Decimal = Query(settings["SLEEP_GOAL_HOURS"], ge=4, le=12),
    ):
        values = [Decimal(value) for value in nights.split(",")]
        if any(value > 16 for value in values):
            reject("night_too_long", "Ночь не может длиться больше 16 часов")
        total = sum(values, Decimal(0))
        required = need * len(values)
        result = estimate(
            nights_count=len(values), total_hours=rounded_hours(total),
            required_hours=rounded_hours(required),
            debt_hours=rounded_hours(max(Decimal(0), required - total)),
            surplus_hours=rounded_hours(max(Decimal(0), total - required)),
            average_hours=rounded_hours(total / len(values)),
            short_nights=sum(value < settings["SLEEP_GOAL_HOURS"] for value in values),
            worst_night_hours=rounded_hours(min(values)),
        )
        logger.info("debt_calculated nights=%d", len(values))
        return result

    @application.get("/sleep/social-jetlag")
    def social_jetlag(
        work_bed: str = Query(..., pattern=TIME_PATTERN),
        work_wake: str = Query(..., pattern=TIME_PATTERN),
        free_bed: str = Query(..., pattern=TIME_PATTERN),
        free_wake: str = Query(..., pattern=TIME_PATTERN),
    ):
        midpoints = []
        for bed, wake in ((work_bed, work_wake), (free_bed, free_wake)):
            start = to_minutes(bed)
            duration = (to_minutes(wake) - start) % DAY_MINUTES
            if not 60 <= duration <= 960:
                reject(
                    "invalid_sleep_duration",
                    "Продолжительность сна должна быть от 1 до 16 часов",
                )
            midpoints.append((start + duration // 2) % DAY_MINUTES)
        difference = abs(midpoints[1] - midpoints[0]) % DAY_MINUTES
        jetlag = min(difference, DAY_MINUTES - difference)
        result = estimate(
            work_midpoint=clock(midpoints[0]), free_midpoint=clock(midpoints[1]),
            jetlag_minutes=jetlag, jetlag=f"{jetlag // 60}:{jetlag % 60:02d}",
            over_one_hour=jetlag >= 60, context=SOCIAL_CONTEXT,
        )
        logger.info("social_jetlag_calculated")
        return result

    @application.get("/sleep/caffeine")
    def caffeine(
        bedtime: str = Query(..., pattern=TIME_PATTERN),
        hours_before: Decimal = Query(settings["CAFFEINE_CUTOFF_HOURS"], ge=3, le=12),
    ):
        # Для дробного числа часов перевод в целые минуты тоже HALF_UP.
        minutes_before = int((hours_before * 60).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        result = estimate(
            bedtime=clock(to_minutes(bedtime)),
            last_coffee=clock(to_minutes(bedtime) - minutes_before),
            hours_before=rounded_hours(hours_before), basis=CAFFEINE_BASIS,
        )
        logger.info("caffeine_calculated")
        return result

    @application.get("/sleep/shift-plan")
    def shift_plan(
        current: str = Query(..., pattern=TIME_PATTERN),
        target: str = Query(..., pattern=TIME_PATTERN),
        step: int = Query(15, ge=5, le=60),
    ):
        start, finish = to_minutes(current), to_minutes(target)
        earlier = (start - finish) % DAY_MINUTES
        later = (finish - start) % DAY_MINUTES
        direction = "earlier" if earlier <= later else "later"
        difference = min(earlier, later)
        days = (difference + step - 1) // step
        if days > 60:
            reject("shift_too_long", "Слишком долгий переход, увеличьте step")
        sign = -1 if direction == "earlier" else 1
        plan = [
            {"day": day, "bedtime": clock(start + sign * min(day * step, difference))}
            for day in range(1, days + 1)
        ]
        result = estimate(
            direction=direction, total_shift_minutes=difference,
            days=days, plan=plan, context=SHIFT_CONTEXT,
        )
        if difference == 0:
            result["message"] = "Режим уже совпадает"
        logger.info("shift_plan_calculated days=%d", days)
        return result

    return application


app = create_app()
