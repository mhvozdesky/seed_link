# SeedLink

Локальна Windows-програма для перевірки чотирьох XLSX-експортів Salesforce і
побудови звітів Seed Selector. Наразі реалізовано Block 01: пакет, доменні
моделі, нормалізації, схеми, контрольні fixtures, налаштування, ресурсні шляхи
та безпечну діагностику. Імпорт XLSX і бізнесовий розрахунок належать наступним
блокам.

## Розробка

Проєкт використовує CPython 3.14.7 (також зафіксований у `.python-version`) та
наявне середовище `.venv`.

```bash
.venv/bin/python -m pip install -r requirements/test.lock
.venv/bin/python -m pytest
.venv/bin/python -m seedlink --version
```

`requirements/runtime.lock`, `requirements/test.lock` і
`requirements/build.lock` містять окремі точні набори runtime, тестових та
пакувальних залежностей. Реальні книги не входять до Python-пакета і не повинні
додаватися до Windows-дистрибутива.
