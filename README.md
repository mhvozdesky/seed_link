# SeedLink

Локальна Windows-програма для перевірки чотирьох XLSX-експортів Salesforce і
побудови звітів Seed Selector. Реалізовано Blocks 01–02: пакет і доменні
контракти, а також суворий імпорт чотирьох XLSX із рольовими схемами,
provenance, SHA-256, нормалізацією потрібних типів та правилами дублів.
Зіставлення людей/ваучерів і бізнесові показники належать наступним блокам.

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
