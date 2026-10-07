"""Учётные записи для нагрузочного теста (loadtest/): N подтверждённых кандидатов + HR-аккаунты при демо-компаниях.

    docker compose exec backend python scripts/loadtest_accounts.py --candidates 200 --out /app/var/loadtest_accounts.json

Кандидаты `lt0001@loadtest.example` … создаются прямо в БД (как seed демо-мира: без писем с кодом), пароли выводятся из
VZ_APP_SECRET (app.services.demo.accounts.password_for), поэтому восстанавливаются без БД. Работодатели берутся из
демо-мира: сначала `make demo`. В stdout пароли не печатаются, только файл --out (в .gitignore: backend/var/, loadtest/*.json).
Не запускается при VZ_ENV=prod.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # запуск как `python scripts/loadtest_accounts.py`

from sqlalchemy import select  # noqa: E402

from app.core.settings import get_settings  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.services.demo import accounts  # noqa: E402
from app.services.demo.profiles import ensure_consents  # noqa: E402

EMAIL_DOMAIN = "loadtest.example"


def main() -> int:
    p = argparse.ArgumentParser(description="Аккаунты для нагрузочного теста")
    p.add_argument("--candidates", type=int, default=200)
    p.add_argument("--employers", type=int, default=40, help="Дополнительные HR-аккаунты (лимит запросов считается на аккаунт)")
    p.add_argument("--offset", type=int, default=0, help="Сдвиг номеров: свежая партия аккаунтов для нового прогона")
    p.add_argument("--out", default="var/loadtest_accounts.json")
    args = p.parse_args()

    settings = get_settings()
    if settings.env == "prod":
        print("Отказ: не запускается при VZ_ENV=prod")
        return 2
    db = get_sessionmaker()()
    try:
        cands = []
        for i in range(args.offset + 1, args.offset + args.candidates + 1):
            email = f"lt{i:04d}@{EMAIL_DOMAIN}"
            pwd = accounts.password_for(settings, f"loadtest:{i}")
            user = accounts.ensure_user(db, settings, email=email, role=UserRole.candidate.value, password=pwd)
            ensure_consents(db, user)
            cands.append({"email": email, "password": pwd})
            if i % 50 == 0:
                db.commit()
        db.commit()
        # Лимит запросов подбора считается на аккаунт (30 в минуту): у реальных HR-ов аккаунты свои, поэтому берём
        # дополнительные аккаунты при демо-компаниях (по кругу), а не гоняем всех виртуальных HR под шестью логинами
        companies = list(db.scalars(select(Company).where(Company.is_demo.is_(True)).order_by(Company.created_at, Company.id)))
        employers: list[dict] = []
        for i in range(1, args.employers + 1 if companies else 1):
            email = f"lthr{i:03d}@{EMAIL_DOMAIN}"
            pwd = accounts.password_for(settings, f"loadtest-hr:{i}")
            accounts.ensure_user(db, settings, email=email, role=UserRole.employer.value, password=pwd,
                                 company=companies[(i - 1) % len(companies)])
            employers.append({"email": email, "password": pwd})
        db.commit()
    finally:
        db.close()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"candidates": cands, "employers": employers}, ensure_ascii=False), encoding="utf-8")
    print(f"Кандидатов: {len(cands)}, работодателей: {len(employers)} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
