import os
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

app = FastAPI(
    title="ASCII Pac-Man",
    description="Multiplayer ASCII Pac-Man game using WebSockets",
    version="0.1"
)

# ===== GAME DATA =====
MAP = [
    "#################",
    "#...............#",
    "#.###.....###...#",
    "#...............#",
    "#################"
]


WIDTH = len(MAP[0])
HEIGHT = len(MAP)

DIRECTIONS = {
    "up": (0, -1),
    "down": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}

players = {}
player_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
start_positions = [(1,1), (18,1), (1,13), (18,13), (9,7), (10,7)]

# ===== GAME LOGIC =====
def is_wall(x, y):
    return MAP[y][x] == "#"

def render_board():
    board = [list(row) for row in MAP]
    for player in players.values():
        board[player["y"]][player["x"]] = player["char"]
    return "\n".join("".join(row) for row in board)

def move_player(player, direction):
    if direction not in DIRECTIONS:
        return
    dx, dy = DIRECTIONS[direction]
    nx, ny = player["x"] + dx, player["y"] + dy
    if 0 <= nx < WIDTH and 0 <= ny < HEIGHT:
        if not is_wall(nx, ny):
            player["x"], player["y"] = nx, ny

# ===== HTTP ROUTE =====
@app.get("/")
async def index():
    return FileResponse("index.html")

# ===== WEBSOCKET ROUTE =====
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    char = player_chars[len(players)]
    pos_index = len(players) % len(start_positions)
    start_x, start_y = start_positions[pos_index]
    players[ws] = {"x": start_x, "y": start_y, "char": char}

    # Keep-alive ping to prevent idle timeout
    async def keep_alive():
        try:
            while True:
                await asyncio.sleep(15)
                await ws.send_text("ping")
        except:
            return

    asyncio.create_task(keep_alive())

    try:
        await ws.send_text(render_board())
        while True:
            msg = await ws.receive_text()
            if msg in ("up", "down", "left", "right"):
                move_player(players[ws], msg)

                board = render_board()
                # Broadcast updated board to all players
                for client in players:
                    await client.send_text(board)

    except WebSocketDisconnect:
        del players[ws]

# ===== ENTRY POINT FOR LOCAL TESTING =====
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
