import json
import redis
import random
import asyncio
import os

from channels.layers import get_channel_layer
from asgiref.sync import sync_to_async

# Game Settings imports
from games_worker.utils.game_config import GameConfig, GameStatus
from games_worker.utils.ball import Ball
from games_worker.utils.player import Player
from games_app.models.game_model import GameModel
from games_app.models.player_model import PlayerModel
from games_app.models.score_model import ScoreModel


redis_client = redis.Redis(host=os.environ.get("REDIS_HOST", "localhost"), port=int(os.environ.get("REDIS_PORT", 6379)), db=0, decode_responses=True)

class GameSession:
    def __init__(self, players, gameId, roomId):
        self.user_session_queue = "user-session-game-queue"
        self.game_status = GameStatus.PLAYING
        self.roomId = roomId
        self.channel_layer = get_channel_layer()
        self.tasks_movement_player = []
        self.gameId = gameId
        self.game = f"game_session_{gameId}"
        self.numberOfPlayers = len(players)
        self.last_player_hit = None
        
        self.players = {}
        orientations = ["left", "right", "top", "bottom"]
        for index, player in enumerate(players):
            if self.numberOfPlayers == 2:
                position = GameConfig.player_positions[index]
            else:
                position = GameConfig.multiplayer_positions[index]
            orientation = orientations[index]
            self.players[player["id"]] = Player(player["id"], player["color"], position["x"], position["y"], orientation)
            if self.numberOfPlayers == 4 and index >= 2:
                self.players[player["id"]].width = GameConfig.multiplayer_width
                self.players[player["id"]].height = GameConfig.multiplayer_height

        self.ball = Ball()
        if self.numberOfPlayers == 4:
            self.ball.x = GameConfig.screen_height // 2
        self.ball_direction = {
            "x": random.choice([-GameConfig.ball_speed, GameConfig.ball_speed]),
            "y": random.choice([-GameConfig.ball_speed, GameConfig.ball_speed])
        }

    async def add_player_channels(self):
        for player in self.players.values():
            self.tasks_movement_player.append(asyncio.create_task(self.process_player_move(player)))

    async def move_ball(self):
        self.ball.x += self.ball_direction["x"]
        self.ball.y += self.ball_direction["y"]
    
    async def check_screen_collision(self, y):
        if y <= 0 or y >= GameConfig.screen_height:
            self.ball_direction["y"] *= -1

    async def check_player_collision(self, x, y):
        # for player in self.players.values():
        #     if player.x <= x <= player.x + player.width:
        #         if player.y <= y <= player.y + player.height:
        #             self.ball_direction["x"] *= -1

        for player in self.players.values():
            if player.orientation in ["left", "right"]:  
                if player.x <= x <= player.x + player.width:
                    if player.y <= y <= player.y + player.height:
                        self.ball_direction["x"] *= -1
                        self.last_player_hit = player

            elif player.orientation in ["top", "bottom"]:
                if player.y <= y <= player.y + player.height:
                    if player.x <= x <= player.x + player.width:
                        self.ball_direction["y"] *= -1
                        self.last_player_hit = player

    async def ball_reset(self):
        self.ball.x = GameConfig.screen_width // 2
        self.ball.y = GameConfig.screen_height // 2
        if self.numberOfPlayers == 4:
            self.ball.x = GameConfig.screen_height // 2
        self.ball_direction = {
            "x": random.choice([-GameConfig.ball_speed, GameConfig.ball_speed]),
            "y": random.choice([-GameConfig.ball_speed, GameConfig.ball_speed])
        }
        self.last_player_hit = None
        asyncio.create_task(self.await_for_new_match())

    async def await_for_new_match(self):
        self.game_status = GameStatus.WAITING
        await asyncio.sleep(5)
        self.game_status = GameStatus.PLAYING

    async def update_ball_position(self):
        if self.game_status == GameStatus.PLAYING:
            ball_x = self.ball.x + self.ball_direction["x"]
            ball_y = self.ball.y + self.ball_direction["y"]
            
            if self.numberOfPlayers == 2:
                await self.check_screen_collision(ball_y)
            await self.check_player_collision(ball_x, ball_y)
            if (await self.check_game_conditions() == True):
                return True

            await self.move_ball()
        return False
    
    async def check_game_conditions(self):
        if self.last_player_hit is None:
            players = (list)(self.players.values())
            if self.ball_direction["x"] > 0:
                self.last_player_hit = next(filter(lambda player: player.orientation == "left", players), None)
            else:
                self.last_player_hit = next(filter(lambda player: player.orientation == "right", players), None)
        if self.ball.x <= 0 or self.ball.x >= GameConfig.screen_width:
            player = self.last_player_hit
            # color = 0
            # if self.ball.x <= 0:
            #     color = 1
            players = (list)(self.players.values())
            # player = next(filter(lambda player: player.color == color, players), None)


            await self.update_score(player)
            if any(player.score >= GameConfig.max_score for player in players):
                return True
            await self.ball_reset()
            if await self.check_players_connected() == True:
                return True

        return False
    
    async def check_players_connected(self):
        players = await sync_to_async(list)(PlayerModel.objects.filter(gameId=self.gameId))
        time = 0
        while (time < 180 and all(player.is_connected == False for player in players)):
            await asyncio.sleep(1)
            time += 1
        if time >= 180:
            return True
        return False

    async def update_score(self, player):
        p = await sync_to_async(PlayerModel.objects.filter(id=player.user_id).first)()
        p.score += 1
        player.score = p.score
        await sync_to_async(p.save)()
        await self.channel_layer.group_send(
            self.game,
            {
                "type": "update_score",
                "playerColor": p.color,
                "playerScore": p.score,
                "expiry": 0.02
            }
        )

    async def notify_clients(self):
        response_data = {
            "ball": self.ball.to_dict(),
            "players": {player.color: player.to_dict() for player in self.players.values()},
            "numberOfPlayers": len(self.players),
        }
        json_response = json.dumps(response_data)

        await self.channel_layer.group_send(
            self.game,
            {
            "type": "game.update",
            "game_state": json_response,
            "expiry": 0.02
            }
        )
    
    async def process_player_move(self, player):
        queue_name = f"{self.game}_{player.user_id}"
        while True:
            await asyncio.sleep(0.005)
            try:
                message = redis_client.lpop(queue_name)
                if message is None:
                    continue
                data = json.loads(message)

                move = data.get("move", {})
                if player.orientation == "left" or player.orientation == "right":
                    player.y += move["direction"] * GameConfig.player_speed
                else:
                    player.x += move["direction"] * GameConfig.player_speed
                if player.orientation in ["left", "right"]:
                    if player.y < 0:
                        player.y = 0
                    elif player.y > GameConfig.screen_height - player.height:
                        player.y = GameConfig.screen_height - player.height
                elif player.orientation in ["top", "bottom"]:
                    if player.x < 0:
                        player.x = 0
                    elif player.x > GameConfig.screen_height - player.width:
                        player.x = GameConfig.screen_height - player.width

            except json.JSONDecodeError as e:
                print(f"Erro ao decodificar JSON: {e}")
            except Exception as e:
                print(f"Erro ao processar mensagem: {e}")

    async def finish_game(self):
        await sync_to_async(GameModel.objects.filter(id=self.gameId).update)(status=1)
        await sync_to_async(PlayerModel.objects.filter(gameId=self.gameId).update)(is_connected=False)
        await self.channel_layer.group_send(
            self.game,
            {
            "type": "game_finished",
            "expiry": 0.02
            }
        )

    async def game_loop(self):
        print("Game loop started")
        asyncio.create_task(self.await_for_new_match())

        while True:
            if await self.update_ball_position() == True:
                break
            await self.notify_clients()
            await asyncio.sleep(0.02)

    async def send_message_game_start(self):
        queue_name = f"room_{self.roomId[:8]}"
        print(f"Sending message game start {queue_name}")
        await self.channel_layer.group_send(
            queue_name,
            {
            "type": "game.started",
            "gameId": self.gameId,
            "expiry": 0.02
            }
        )

        game = await sync_to_async(GameModel.objects.filter(id=self.gameId).first)()
    
        while True:
            all_players_connected = 0
            players = await sync_to_async(list)(game.players.all())
            for player in players:
                if player.is_connected == True:
                    all_players_connected += 1
            if all_players_connected == self.numberOfPlayers:
                break
            await asyncio.sleep(2)

    async def startGame(self):
        print("Game started")
        await self.add_player_channels()
        await self.send_message_game_start()
        await self.game_loop()
        await self.finish_game()