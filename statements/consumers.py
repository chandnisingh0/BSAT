"""
WebSocket consumer for live statement-extraction progress.

Each browser tab viewing /statement/<id>/ opens a WebSocket to
ws/statement/<id>/, joins a group named statement_<id>, and receives
push events whenever the Celery task processing that statement makes
progress (new page OCR'd, new rows saved, or job finished).
"""
import json
from channels.generic.websocket import AsyncWebsocketConsumer


class StatementProgressConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.statement_id = self.scope["url_route"]["kwargs"]["statement_id"]
        self.group_name = f"statement_{self.statement_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # Called when the Celery task sends a "statement.progress" event
    async def statement_progress(self, event):
        await self.send(text_data=json.dumps(event["data"]))