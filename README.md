# SeedLink

Локальна Windows-програма для перевірки чотирьох XLSX-експортів Salesforce і
побудови звітів Seed Selector. Реалізовано Blocks 01–03: пакет і доменні
контракти, суворий імпорт чотирьох XLSX, а також автоматичне зіставлення людей,
опитувань, згадок і повних ваучерів із provenance та явними проблемами.
Кількості, дати, воронка й інші бізнесові показники належать Block 04.

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
