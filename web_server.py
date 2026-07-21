"""
web_server.py — Простой веб-сервер для Render.com

Запускает HTTP-сервер на порту 10000 + AI-агента в фоне.
Render.com требует веб-сервер для бесплатного хостинга.
"""

import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Импортируем и запускаем AI-агента в фоне
from vk_agent import main as start_agent


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'VK AI Agent is running!')

    def log_message(self, format, *args):
        pass  # Отключаем логи HTTP-запросов


def run_server():
    port = int(os.getenv('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f'HTTP-сервер запущен на порту {port}')
    server.serve_forever()


if __name__ == '__main__':
    # Запускаем AI-агента в отдельном потоке
    agent_thread = threading.Thread(target=start_agent, daemon=True)
    agent_thread.start()
    print('AI-агент запущен в фоне')

    # Запускаем веб-сервер (блокирующий)
    run_server()
