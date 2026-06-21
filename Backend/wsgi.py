from a2wsgi import ASGIMiddleware
from main import app as fastapi_app

# Оборачиваем твое ASGI-приложение в WSGI-обертку
application = ASGIMiddleware(fastapi_app)

# Дублируем переменную под именем app (на случай, если хостинг ищет именно её)
app = application