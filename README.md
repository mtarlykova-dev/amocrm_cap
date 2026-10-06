# amocrm_cap — доступ к amoCRM через API-прокси 4teenHub

4teenHub (внутренняя платформа) даёт **только чтение** данных amoCRM через свой прокси. Настоящий OAuth-токен amoCRM при этом не выдаётся.
Здесь лежит клиент на Python без зависимостей (`amo_proxy.py`) и CLI к нему.

## Ключ

- Ключ персональный. Его выдаёт админ в разделе «API-доступ» на https://4teenhub.ru.
- **Ключ — секрет.** Не кладите его в код или репозиторий и не пишите в чат, если канал не защищённый.
  Клиент берёт ключ только из переменной окружения `FOURTEENHUB_API_KEY`. Файлы `.env` и `*.key` есть в `.gitignore`.
- Если ключ скомпрометирован, попросите админа отозвать его (одна кнопка) и выдать новый.

```bash
cp .env.example .env        # впишите ключ в .env
set -a; . ./.env; set +a    # или просто: export FOURTEENHUB_API_KEY=...
```

## Правила прокси

| | |
|---|---|
| Базовый адрес | `https://4teenhub.ru/amo-api/` |
| Заголовок | `X-API-Key: <ключ>` |
| Методы | только `GET`, остальные дают `405` |
| Пути | как в amoCRM API v4: `https://<поддомен>.amocrm.ru/api/v4/leads/1` → `https://4teenhub.ru/amo-api/api/v4/leads/1` |
| Белый список | `account, leads, contacts, companies, tasks, events, notes, pipelines, users, custom_fields, tags, customers, segments`, остальные дают `403` |
| Пагинация | `_links.next` уже ведёт на домен прокси, по ссылке можно переходить с тем же ключом |
| Лимит всплеска | `429`: подождать и повторить (клиент делает это сам, с экспоненциальной задержкой) |
| Ошибки amoCRM | `400`, `404` и т.п. приходят как есть, подробности в поле `detail` тела ответа |
| Логирование | каждый запрос (время, путь, статус, IP) виден админу |

## curl

```bash
curl -s "https://4teenhub.ru/amo-api/api/v4/leads?limit=5" \
  -H "X-API-Key: $FOURTEENHUB_API_KEY"
```

## CLI

```bash
python amo_proxy.py account
python amo_proxy.py leads --limit 5
python amo_proxy.py leads/26644877
python amo_proxy.py leads/26644877 -p with=contacts
python amo_proxy.py contacts --all -p query=Иван        # все страницы, плоский список
python amo_proxy.py leads/pipelines
```

Результат выводится как JSON в stdout. Ошибки идут в stderr, код выхода при этом 1.

## Python

```python
from amo_proxy import AmoProxyClient, AmoProxyError

client = AmoProxyClient()                     # ключ из $FOURTEENHUB_API_KEY

lead = client.get("leads/26644877", {"with": "contacts"})

for contact in client.iter_items("contacts", {"limit": 250}):   # обходит все страницы
    print(contact["id"], contact["name"])

try:
    client.get("leads/1")
except AmoProxyError as e:
    print(e.status, e.body)                   # тело ответа amoCRM/прокси как есть
```

Защита на стороне клиента:
- Раздел, которого нет в белом списке, отсекается ещё до запроса (`ValueError`).
- Ключ отправляется только на базовый адрес прокси. Ссылка на чужой домен даст `ValueError`.
- Методов записи в клиенте нет.

## Тесты

```bash
python -m unittest discover -s tests -v
```
