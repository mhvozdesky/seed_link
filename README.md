# SeedLink

Локальна Windows-програма для перевірки чотирьох XLSX-експортів Salesforce і
побудови звітів Seed Selector. Реалізовано Blocks 01–05: пакет і доменні
контракти, суворий імпорт чотирьох XLSX, а також автоматичне зіставлення людей,
опитувань, згадок і повних ваучерів із provenance та явними проблемами;
кількості, дати, воронка, клієнти, культури, інші ваучери й незалежні вибірки
зведено в незмінний `ReportResult`. Сеансовий сервіс додає ручні рішення,
повний перерахунок ревізій, undo, progress, cancellation та контроль
актуальності експорту без збереження рішень між запусками.

## Розробка

Проєкт використовує CPython 3.14.7 (також зафіксований у `.python-version`) та
наявне середовище `.venv`.

```bash
.venv/bin/python -m pip install -r requirements/test.lock
.venv/bin/python -m pytest
.venv/bin/python -m seedlink --version
```

Програмний контракт імпорту:

```python
from seedlink.domain.provenance import InputRole
from seedlink.input_xlsx import import_workbooks

result = import_workbooks(
    {
        InputRole.R1: "r1.xlsx",
        InputRole.R2: "r2.xlsx",
        InputRole.R3: "r3.xlsx",
        InputRole.R4: "r4.xlsx",
    }
)
if result.is_accepted:
    snapshot = result.snapshot
else:
    blocking_issues = result.blocking_issues
```

`requirements/runtime.lock`, `requirements/test.lock` і
`requirements/build.lock` містять окремі точні набори runtime, тестових та
пакувальних залежностей. Реальні книги не входять до Python-пакета і не повинні
додаватися до Windows-дистрибутива.

Після успішного імпорту автоматичне зіставлення запускається окремо:

```python
from seedlink.application import analyze_links

matching = analyze_links(result)
accepted_voucher_keys = matching.accepted_voucher_keys
review_issues = matching.issues
```

Повний автоматичний результат і вибірки Block 04:

```python
from seedlink.application import build_report_result
from seedlink.domain import ProductFilter, query_product_lines

report = build_report_result(matching)
visible_products = query_product_lines(report, ProductFilter())
```

Сеанс із ручним рішенням і undo:

```python
from seedlink.application import SeedLinkSession

session = SeedLinkSession()
imported = session.import_inputs(paths_by_role)
if imported.is_accepted:
    automatic = session.analyze()
    revised = session.select_vouchers(
        automatic.surveys[0].key,
        (automatic.vouchers[0].key,),
        reason="Перевірено за вихідним записом",
    )
    restored = session.undo_decision(revised.decisions[0].decision_id)
```

Деталі контракту й перевірок наведено в `docs/block_05_uk.md`.
