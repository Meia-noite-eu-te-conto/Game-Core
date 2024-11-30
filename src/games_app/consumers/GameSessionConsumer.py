import logging
import json
import redis

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer
from asgiref.sync import sync_to_async
from games_app.models.game_model import GameModel
from games_app.models.player_model import PlayerModel

logger = logging.getLogger(__name__)

redis_client = redis.Redis(host='redis', port=6379, db=0)

class GameSessionConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.gameId = self.scope['url_route']['kwargs']['game_id']
        cookies_header = dict(self.scope['headers']).get(b'cookie', b'').decode('utf-8')
        cookies = dict(item.split("=") for item in cookies_header.split("; ") if "=" in item)
        self.userId = cookies.get("userId")        
        self.game_channel = f"game_session_{self.gameId}"
        self.player_channel = f"{self.game_channel}_{self.userId}"

        logger.info(f"| Stating | connect | User {self.userId} connecting {self.gameId}.")

        game = await sync_to_async(GameModel.objects.filter(id=self.gameId).first)()
        if game is None:
            logger.info(f"User {self.userId} disconnected from game {self.gameId} - Game not found")
            await self.close()
            return

        user = await sync_to_async(PlayerModel.objects.filter(gameId=self.gameId, id=self.userId).first)()
        if user is None:
            logger.info(f"User {self.userId} disconnected from game {self.gameId} - User not found")
            await self.close(code="204")
            return

        user.is_connected = True
        await sync_to_async(user.save)()

        await self.channel_layer.group_add(
            self.game_channel, 
            self.channel_name)
        
        await self.accept()

        self.players = await sync_to_async(list)(game.players.all())

        for player in self.players:
            await self.channel_layer.group_send(
                self.game_channel,
                {
                    "type": "update_score",
                    "playerColor": player.color,
                    "playerScore": player.score,
                    "expiry": 0.02
                }
            )
        logger.info(f"| Finished | connect | User {self.userId} connected {self.gameId}.")

    async def disconnect(self, close_code):
        user = await sync_to_async(PlayerModel.objects.filter(gameId=self.gameId, id=self.userId).first)()
        if user:
            user.is_connected = False
            await sync_to_async(user.save)()

        await self.channel_layer.group_discard(
            self.game_channel,
            self.channel_name)

    async def receive(self, text_data):
        
        logger.info(f"Stating | {GameSessionConsumer.__name__} | receive | User {self.userId} send a movement to {self.gameId}.")
        
        data = json.loads(text_data)
        direction = data.get('direction')

        if direction in ["w", "s"]:
            move = {
                "userId": self.userId,
                "direction": -1 if direction == "w" else 1
            }
            logger.info(f"User {self.userId} moved to {move['direction']}")
            message = {'type': 'player.move', "move": move}
            redis_client.rpush(self.player_channel, json.dumps(message))

        logger.info(f"Finished | {GameSessionConsumer.__name__} | receive | User {self.userId} send a movement to {self.gameId}.")

    async def game_update(self, event):
        #logger.info(f"Starting | {GameSessionConsumer.__name__} | game_update | User {self.userId} received a game update for {self.gameId}.")
        
        response = json.loads(event["game_state"])
        await self.send(
            text_data=json.dumps({
                "type": event["type"],
                "game_state": json.dumps(response)
            })
        )
    
    async def update_score(self, event):
        await self.send(text_data=json.dumps(event))

    async def game_finished(self, event):
        await self.send(text_data=json.dumps(event))