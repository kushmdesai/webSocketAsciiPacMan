import os
import asyncio
import random
import copy
import uuid
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from typing import Dict, List, Tuple

app = FastAPI(title="ASCII Pac-Man Multiplayer - Full Featured", version="1.0")

# ===== PAC-MAN MAP =====
RAW_MAP = [
    "############################",
    "#............##............#",
    "#.####.#####.##.#####.####.#",
    "#@#  #.#   #.##.#   #.#  #@#",
    "#.####.#####.##.#####.####.#",
    "#..........................#",
    "#.####.##.########.##.####.#",
    "#.####.##.########.##.####.#",
    "#......##....##....##......#",
    "######.##### ## #####.######",
    "     #.##### ## #####.#     ",
    "     #.##          ##.#     ",
    "     #.## ###--### ##.#     ",
    "######.## #GGGGGG# ##.######",
    "T     .   #GGGGGG#   .     T",
    "######.## #GGGGGG# ##.######",
    "     #.## ######## ##.#     ",
    "     #.##          ##.#     ",
    "     #.## ######## ##.#     ",
    "######.## ######## ##.######",
    "#............##............#",
    "#.####.#####.##.#####.####.#",
    "#.####.#####.##.#####.####.#",
    "#@..##.......  .......##..@#",
    "###.##.##.########.##.##.###",
    "###.##.##.########.##.##.###",
    "#......##....##....##......#",
    "#.##########.##.##########.#",
    "#.##########.##.##########.#",
    "#..........................#",
    "############################"
]

# ===== GAME STATE =====
GAME_MAP = [list(row) for row in RAW_MAP]
game_level = 1
game_over = False
winner = None

# ===== LOBBY SYSTEM =====
lobby = {}
session_to_ws = {}
roles_taken = {"Pac-Man": 0, "Ghost": 0}
game_started = False

# ===== PLAYERS =====
players = {}
player_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PACMAN_SPAWNS = [(1,1),(26,1),(1,29),(26,29)]
GHOST_SPAWNS = [(12,14),(13,14),(14,14),(15,14)]
GHOST_PEN_EXITS = [(13,11),(14,11)]

# ===== AI GHOSTS =====
ai_ghosts = []
GHOST_NAMES = ["Blinky", "Pinky", "Inky", "Clyde"]
GHOST_BEHAVIORS = ["chase", "ambush", "random", "patrol"]
ghost_mode = "scatter"  # "scatter" or "chase"
ghost_mode_timer = 0

# ===== FRUIT SYSTEM =====
fruits = []  # {"x", "y", "type", "points", "spawn_time"}
FRUIT_TYPES = [
    {"char": "C", "name": "Cherry", "points": 100},
    {"char": "S", "name": "Strawberry", "points": 300},
    {"char": "O", "name": "Orange", "points": 500},
    {"char": "A", "name": "Apple", "points": 700},
    {"char": "M", "name": "Melon", "points": 1000},
]
FRUIT_DURATION = 10  # seconds
pellets_eaten_for_fruit = 0
PELLETS_PER_FRUIT = 30

# ===== POWER PELLET SETTINGS =====
POWER_PELLET_DURATION = 10
GHOST_DEATH_SCORES = [200, 400, 800, 1600]  # Multiplier for consecutive ghost kills
POWER_FLASH_WARNING = 3  # Start flashing 3 seconds before end

# ===== LIVES SYSTEM =====
STARTING_LIVES = 3

# ===== SPEED SETTINGS =====
PACMAN_SPEED = 0.15  # Base delay between moves
PACMAN_EATING_SPEED = 0.18  # Slower when eating
GHOST_SPEED = 0.18
GHOST_TUNNEL_SPEED = 0.25  # Slower in tunnels
GHOST_FRIGHTENED_SPEED = 0.25  # Slower when frightened
AI_GHOST_UPDATE_INTERVAL = 0.5

# ===== DIRECTIONS =====
DIRECTIONS = {"up":(0,-1),"down":(0,1),"left":(-1,0),"right":(1,0)}

# ===== ROLE LIMITS =====
MAX_PACMAN_RATIO = 4

def can_select_role(role):
    current_pacman = roles_taken["Pac-Man"]
    current_ghosts = roles_taken["Ghost"]
    
    if role == "Pac-Man":
        if current_pacman == 0:
            return True
        return current_ghosts >= (current_pacman * MAX_PACMAN_RATIO)
    elif role == "Ghost":
        return True
    return False

# ===== GAME FUNCTIONS =====
def reset_game():
    global GAME_MAP, game_level, game_over, winner, pellets_eaten_for_fruit, fruits
    global ghost_mode, ghost_mode_timer
    GAME_MAP = [list(row) for row in RAW_MAP]
    game_level = 1
    game_over = False
    winner = None
    pellets_eaten_for_fruit = 0
    fruits = []
    ghost_mode = "scatter"
    ghost_mode_timer = time.time()
    
    # Reset all player stats
    for player in players.values():
        player["score"] = 0
        player["lives"] = STARTING_LIVES
        player["powered_up_until"] = 0
        player["ghosts_eaten_combo"] = 0
        player["is_alive"] = True
        respawn_player(player)

def count_pellets():
    """Count remaining pellets on the map"""
    count = 0
    for row in GAME_MAP:
        for cell in row:
            if cell in ['.', '@']:
                count += 1
    return count

def is_wall(x, y):
    if y < 0 or y >= len(GAME_MAP) or x < 0 or x >= len(GAME_MAP[0]):
        return True
    return GAME_MAP[y][x] == "#"

def is_tunnel(x, y):
    """Check if position is in a tunnel (marked with T)"""
    if y < 0 or y >= len(RAW_MAP) or x < 0 or x >= len(RAW_MAP[0]):
        return False
    return RAW_MAP[y][x] == "T"

def is_ghost_pen(x, y):
    """Check if position is in ghost pen"""
    if y < 0 or y >= len(RAW_MAP) or x < 0 or x >= len(RAW_MAP[0]):
        return False
    return RAW_MAP[y][x] == "G"

def wrap_position(x, y):
    """Handle tunnel wrapping"""
    # Left tunnel wraps to right
    if x < 0:
        return len(GAME_MAP[0]) - 1, y
    # Right tunnel wraps to left
    if x >= len(GAME_MAP[0]):
        return 0, y
    return x, y

def is_powered_up(player):
    if player["role"] != "Pac-Man":
        return False
    return time.time() < player.get("powered_up_until", 0)

def get_power_time_left(player):
    if not is_powered_up(player):
        return 0
    return max(0, player.get("powered_up_until", 0) - time.time())

def should_flash_power(player):
    """Check if power pellet should flash (warning)"""
    time_left = get_power_time_left(player)
    return 0 < time_left <= POWER_FLASH_WARNING

def respawn_player(player):
    """Respawn a player at their starting position"""
    if player["role"] == "Pac-Man":
        # Find which pacman this is
        pacman_players = [p for p in players.values() if p["role"] == "Pac-Man"]
        idx = pacman_players.index(player) if player in pacman_players else 0
        spawn_x, spawn_y = PACMAN_SPAWNS[idx % len(PACMAN_SPAWNS)]
    else:  # Ghost
        ghost_players = [p for p in players.values() if p["role"] == "Ghost"]
        idx = ghost_players.index(player) if player in ghost_players else 0
        spawn_x, spawn_y = GHOST_SPAWNS[idx % len(GHOST_SPAWNS)]
    
    player["x"], player["y"] = spawn_x, spawn_y
    player["last_move_time"] = time.time()

def spawn_fruit():
    """Spawn a fruit at a random empty location"""
    global fruits
    
    # Find empty spaces
    empty_spaces = []
    for y in range(len(GAME_MAP)):
        for x in range(len(GAME_MAP[0])):
            if GAME_MAP[y][x] == " " and not is_ghost_pen(x, y):
                empty_spaces.append((x, y))
    
    if empty_spaces:
        x, y = random.choice(empty_spaces)
        fruit_type = FRUIT_TYPES[min(game_level - 1, len(FRUIT_TYPES) - 1)]
        fruits.append({
            "x": x,
            "y": y,
            "type": fruit_type["char"],
            "name": fruit_type["name"],
            "points": fruit_type["points"],
            "spawn_time": time.time()
        })

def update_fruits():
    """Remove expired fruits"""
    global fruits
    current_time = time.time()
    fruits = [f for f in fruits if current_time - f["spawn_time"] < FRUIT_DURATION]

def move_player(player, direction):
    global pellets_eaten_for_fruit, game_over, winner
    
    if not player.get("is_alive", True):
        return
    
    # Check movement speed throttle
    current_time = time.time()
    last_move = player.get("last_move_time", 0)
    
    # Determine speed based on state
    if player["role"] == "Pac-Man":
        speed = PACMAN_SPEED
    else:  # Ghost
        if is_tunnel(player["x"], player["y"]):
            speed = GHOST_TUNNEL_SPEED
        elif any(is_powered_up(p) for p in players.values() if p["role"] == "Pac-Man"):
            speed = GHOST_FRIGHTENED_SPEED
        else:
            speed = GHOST_SPEED
    
    if current_time - last_move < speed:
        return  # Too soon to move
    
    player["last_move_time"] = current_time
    
    dx, dy = DIRECTIONS.get(direction, (0, 0))
    nx, ny = player["x"] + dx, player["y"] + dy
    
    # Handle wrapping
    nx, ny = wrap_position(nx, ny)
    
    # Check if valid move
    if 0 <= ny < len(GAME_MAP) and 0 <= nx < len(GAME_MAP[0]):
        # Ghosts can move through ghost pen, others can't
        if is_ghost_pen(nx, ny) and player["role"] == "Pac-Man":
            return
        
        if not is_wall(nx, ny):
            player["x"], player["y"] = nx, ny
            
            # Only Pac-Man can eat pellets and fruits
            if player["role"] == "Pac-Man":
                tile = GAME_MAP[ny][nx]
                
                # Eat regular pellet
                if tile == ".":
                    player["score"] += 10
                    GAME_MAP[ny][nx] = " "
                    pellets_eaten_for_fruit += 1
                    
                    # Check win condition
                    if count_pellets() == 0:
                        game_over = True
                        winner = max(players.values(), key=lambda p: p["score"])
                    
                    # Spawn fruit
                    if pellets_eaten_for_fruit >= PELLETS_PER_FRUIT:
                        spawn_fruit()
                        pellets_eaten_for_fruit = 0
                
                # Eat power pellet
                elif tile == "@":
                    player["score"] += 50
                    player["powered_up_until"] = time.time() + POWER_PELLET_DURATION
                    player["ghosts_eaten_combo"] = 0  # Reset combo
                    GAME_MAP[ny][nx] = " "
                    pellets_eaten_for_fruit += 1
                    
                    # Check win condition
                    if count_pellets() == 0:
                        game_over = True
                        winner = max(players.values(), key=lambda p: p["score"])
                
                # Eat fruit
                for fruit in fruits[:]:
                    if fruit["x"] == nx and fruit["y"] == ny:
                        player["score"] += fruit["points"]
                        fruits.remove(fruit)

def get_ghost_target(ghost, behavior):
    """Get target position for AI ghost based on behavior"""
    # Find nearest Pac-Man
    pacman_players = [p for p in players.values() if p["role"] == "Pac-Man" and p.get("is_alive", True)]
    if not pacman_players:
        return (14, 14)  # Center if no Pac-Man
    
    target_pacman = min(pacman_players, 
                       key=lambda p: abs(p["x"] - ghost["x"]) + abs(p["y"] - ghost["y"]))
    
    if ghost_mode == "scatter":
        # Go to corners
        corners = [(1, 1), (26, 1), (1, 29), (26, 29)]
        ghost_idx = ai_ghosts.index(ghost) if ghost in ai_ghosts else 0
        return corners[ghost_idx % len(corners)]
    
    # Chase mode behaviors
    if behavior == "chase":
        # Direct chase
        return (target_pacman["x"], target_pacman["y"])
    
    elif behavior == "ambush":
        # Target 4 tiles ahead of Pac-Man
        # (simplified - in real Pac-Man this considers direction)
        return (target_pacman["x"] + 4, target_pacman["y"])
    
    elif behavior == "patrol":
        # Patrol a specific area
        patrol_points = [(7, 7), (20, 7), (20, 23), (7, 23)]
        ghost_idx = ai_ghosts.index(ghost) if ghost in ai_ghosts else 0
        return patrol_points[ghost_idx % len(patrol_points)]
    
    else:  # random
        return (random.randint(1, 26), random.randint(1, 29))

def move_ai_ghost(ghost, behavior):
    """Move AI ghost with pathfinding"""
    # If frightened (any Pac-Man powered up), move randomly
    frightened = any(is_powered_up(p) for p in players.values() if p["role"] == "Pac-Man")
    
    if frightened:
        # Random movement when frightened
        directions = list(DIRECTIONS.values())
        random.shuffle(directions)
        for dx, dy in directions:
            nx, ny = ghost["x"] + dx, ghost["y"] + dy
            nx, ny = wrap_position(nx, ny)
            if 0 <= ny < len(GAME_MAP) and 0 <= nx < len(GAME_MAP[0]):
                if not is_wall(nx, ny):
                    ghost["x"], ghost["y"] = nx, ny
                    break
    else:
        # Smart movement toward target
        target_x, target_y = get_ghost_target(ghost, behavior)
        
        best_move = None
        best_dist = float('inf')
        
        for direction, (dx, dy) in DIRECTIONS.items():
            nx, ny = ghost["x"] + dx, ghost["y"] + dy
            nx, ny = wrap_position(nx, ny)
            
            if 0 <= ny < len(GAME_MAP) and 0 <= nx < len(GAME_MAP[0]):
                if not is_wall(nx, ny):
                    dist = abs(nx - target_x) + abs(ny - target_y)
                    if dist < best_dist:
                        best_dist = dist
                        best_move = (nx, ny)
        
        if best_move:
            ghost["x"], ghost["y"] = best_move

def check_collisions():
    """Check for collisions between Pac-Man and Ghosts"""
    global game_over
    
    # Get all ghost positions
    ghost_positions = {}
    
    # AI ghosts
    for ghost in ai_ghosts:
        ghost_positions[(ghost["x"], ghost["y"])] = ("ai", ghost)
    
    # Player ghosts
    for sid, player in players.items():
        if player["role"] == "Ghost" and player.get("is_alive", True):
            ghost_positions[(player["x"], player["y"])] = ("player", sid)
    
    # Check each Pac-Man
    for pac_sid, pacman in list(players.items()):
        if pacman["role"] != "Pac-Man" or not pacman.get("is_alive", True):
            continue
        
        pac_pos = (pacman["x"], pacman["y"])
        
        if pac_pos in ghost_positions:
            ghost_type, ghost_ref = ghost_positions[pac_pos]
            
            if is_powered_up(pacman):
                # Pac-Man eats ghost!
                combo_idx = min(pacman["ghosts_eaten_combo"], len(GHOST_DEATH_SCORES) - 1)
                points = GHOST_DEATH_SCORES[combo_idx]
                pacman["score"] += points
                pacman["ghosts_eaten_combo"] += 1
                
                # Respawn ghost
                if ghost_type == "ai":
                    ghost = ghost_ref
                    ghost["x"], ghost["y"] = GHOST_SPAWNS[ai_ghosts.index(ghost) % len(GHOST_SPAWNS)]
                    ghost["in_pen"] = True
                else:  # player ghost
                    ghost_player = players[ghost_ref]
                    respawn_player(ghost_player)
            else:
                # Ghost catches Pac-Man!
                pacman["lives"] -= 1
                pacman["powered_up_until"] = 0  # Lose power-up
                
                if pacman["lives"] <= 0:
                    pacman["is_alive"] = False
                    # Check if all Pac-Men are dead
                    if not any(p.get("is_alive", True) for p in players.values() if p["role"] == "Pac-Man"):
                        game_over = True
                        # Ghosts win
                        winner = max(
                            (p for p in players.values() if p["role"] == "Ghost"),
                            key=lambda p: p.get("score", 0),
                            default=None
                        )
                else:
                    respawn_player(pacman)

def render_board():
    """Render the game board with all entities"""
    board = copy.deepcopy(GAME_MAP)
    
    # Draw fruits
    for fruit in fruits:
        board[fruit["y"]][fruit["x"]] = fruit["type"]
    
    # Draw AI ghosts
    for ghost in ai_ghosts:
        if not ghost.get("in_pen", False):
            board[ghost["y"]][ghost["x"]] = ghost["char"]
    
    # Draw players
    for player in players.values():
        if player.get("is_alive", True):
            board[player["y"]][player["x"]] = player["char"]
    
    return "\n".join("".join(row) for row in board)

def get_game_state():
    """Get complete game state for clients"""
    power_status = {}
    for sid, player in players.items():
        if player["role"] == "Pac-Man":
            powered = is_powered_up(player)
            time_left = get_power_time_left(player)
            flashing = should_flash_power(player)
            power_status[player["char"]] = {
                "powered": powered,
                "time_left": int(time_left),
                "flashing": flashing
            }
    
    return {
        "board": render_board(),
        "players": players,
        "power_status": power_status,
        "level": game_level,
        "pellets_left": count_pellets(),
        "game_over": game_over,
        "winner": winner["char"] if winner else None,
        "fruits": fruits
    }

# ===== HTTP ROUTE =====
@app.get("/")
async def index():
    return FileResponse("index.html")

# ===== LOBBY WEBSOCKET =====
@app.websocket("/lobby")
async def lobby_ws(ws: WebSocket):
    global game_started
    await ws.accept()
    
    if game_started:
        await ws.send_json({"error": "Game already started"})
        await ws.close()
        return

    session_id = str(uuid.uuid4())
    lobby[session_id] = {"name": f"Player{len(lobby)+1}", "role": None}
    session_to_ws[session_id] = ws

    async def send_lobby():
        data = [{"name": p["name"], "role": p["role"]} for p in lobby.values()]
        for sid in list(lobby.keys()):
            try:
                client_ws = session_to_ws.get(sid)
                if client_ws:
                    await client_ws.send_json({
                        "lobby": data,
                        "roles_taken": roles_taken,
                        "session_id": sid,
                        "can_select_pacman": can_select_role("Pac-Man"),
                        "can_select_ghost": can_select_role("Ghost")
                    })
            except:
                pass

    await send_lobby()

    try:
        while True:
            msg = await ws.receive_json()
            
            if "role" in msg and msg["role"] in ["Pac-Man", "Ghost"]:
                if not can_select_role(msg["role"]):
                    await ws.send_json({
                        "error": f"Cannot select {msg['role']}. Need {MAX_PACMAN_RATIO} ghosts per Pac-Man!"
                    })
                    continue
                
                old_role = lobby[session_id]["role"]
                if old_role:
                    roles_taken[old_role] -= 1
                
                lobby[session_id]["role"] = msg["role"]
                roles_taken[msg["role"]] += 1
                await send_lobby()

            if all(p["role"] for p in lobby.values()) and len(lobby) > 0:
                if roles_taken["Pac-Man"] == 0 or roles_taken["Ghost"] == 0:
                    continue
                    
                game_started = True
                for sid in lobby.keys():
                    client_ws = session_to_ws.get(sid)
                    if client_ws:
                        await client_ws.send_json({"start_game": True, "session_id": sid})
                break
                
    except WebSocketDisconnect:
        old_role = lobby[session_id]["role"]
        if old_role:
            roles_taken[old_role] -= 1
        del lobby[session_id]
        if session_id in session_to_ws:
            del session_to_ws[session_id]
        if not game_started:
            await send_lobby()

# ===== GAME WEBSOCKET =====
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()
    
    if session_id not in lobby:
        await ws.send_text("Error: Invalid session")
        await ws.close()
        return
    
    role = lobby[session_id]["role"]
    char = player_chars[len(players) % len(player_chars)]
    
    if role == "Pac-Man":
        pacman_count = sum(1 for p in players.values() if p["role"] == "Pac-Man")
        spawn_x, spawn_y = PACMAN_SPAWNS[pacman_count % len(PACMAN_SPAWNS)]
    else:
        ghost_count = sum(1 for p in players.values() if p["role"] == "Ghost")
        spawn_x, spawn_y = GHOST_SPAWNS[ghost_count % len(GHOST_SPAWNS)]
    
    players[session_id] = {
        "x": spawn_x,
        "y": spawn_y,
        "char": char,
        "score": 0,
        "role": role,
        "ws": ws,
        "powered_up_until": 0,
        "ghosts_eaten_combo": 0,
        "lives": STARTING_LIVES,
        "is_alive": True,
        "last_move_time": time.time()
    }

    async def keep_alive():
        try:
            while True:
                await asyncio.sleep(15)
                await ws.send_json({"type": "ping"})
        except:
            return
    
    asyncio.create_task(keep_alive())

    try:
        await broadcast_game_state()
        while True:
            msg = await ws.receive_json()
            
            if msg.get("type") == "move" and msg.get("direction") in DIRECTIONS:
                if not game_over:
                    move_player(players[session_id], msg["direction"])
                    check_collisions()
                    await broadcast_game_state()
            
            elif msg.get("type") == "restart":
                if game_over:
                    reset_game()
                    await broadcast_game_state()
                
    except WebSocketDisconnect:
        if session_id in players:
            del players[session_id]

async def broadcast_game_state():
    """Broadcast game state to all players"""
    state = get_game_state()
    
    # Build score display
    score_lines = []
    for p in players.values():
        # Only show lives for Pac-Man
        if p['role'] == "Pac-Man":
            line = f"{p['char']}: {p['score']} pts, Lives: {p['lives']} ({p['role']})"
        else:  # Ghost
            line = f"{p['char']}: {p['score']} pts ({p['role']})"
        
        if p['role'] == "Pac-Man" and p['char'] in state['power_status']:
            ps = state['power_status'][p['char']]
            if ps['powered']:
                line += f" 💪 POWER! ({ps['time_left']}s)"
                if ps['flashing']:
                    line += " ⚠️"
        
        if not p.get('is_alive', True):
            line += " [DEAD]"
        
        score_lines.append(line)
    
    # Add game info
    info_lines = [
        f"Level: {state['level']} | Pellets Left: {state['pellets_left']}"
    ]
    
    if state['fruits']:
        fruit_info = ", ".join([f"{f['name']} ({f['points']}pts)" for f in state['fruits']])
        info_lines.append(f"Fruits: {fruit_info}")
    
    if state['game_over']:
        if state['winner']:
            info_lines.append(f"🎉 GAME OVER! Winner: {state['winner']} 🎉")
        else:
            info_lines.append("🎉 GAME OVER! 🎉")
        info_lines.append("Send 'restart' to play again!")
    
    message = {
        "type": "game_state",
        "board": state['board'],
        "scores": "\n".join(score_lines),
        "info": "\n".join(info_lines),
        "power_status": state['power_status'],
        "game_over": state['game_over']
    }
    
    for player in list(players.values()):
        try:
            await player["ws"].send_json(message)
        except:
            pass

# ===== AI GHOST LOOP =====
async def ai_ghost_loop():
    """Update AI ghosts periodically"""
    global ghost_mode, ghost_mode_timer
    
    await asyncio.sleep(2)
    
    while True:
        await asyncio.sleep(AI_GHOST_UPDATE_INTERVAL)
        
        if game_over or not players:
            continue
        
        # Toggle ghost mode every 20 seconds
        if time.time() - ghost_mode_timer > 20:
            ghost_mode = "chase" if ghost_mode == "scatter" else "scatter"
            ghost_mode_timer = time.time()
        
        # Move AI ghosts
        for i, ghost in enumerate(ai_ghosts):
            behavior = GHOST_BEHAVIORS[i % len(GHOST_BEHAVIORS)]
            move_ai_ghost(ghost, behavior)
        
        # Update fruits
        update_fruits()
        
        # Check collisions
        check_collisions()
        
        # Broadcast state
        await broadcast_game_state()

# ===== START SERVER =====
if __name__ == "__main__":
    import uvicorn
    
    # Initialize AI ghosts
    ai_ghosts = [
        {"x": 12, "y": 13, "char": "B", "in_pen": False},  # Blinky
        {"x": 13, "y": 13, "char": "P", "in_pen": False},  # Pinky
        {"x": 14, "y": 13, "char": "I", "in_pen": False},  # Inky
        {"x": 15, "y": 13, "char": "C", "in_pen": False},  # Clyde
    ]
    
    port = int(os.environ.get("PORT", 8000))
    loop = asyncio.get_event_loop()
    loop.create_task(ai_ghost_loop())
    uvicorn.run(app, host="0.0.0.0", port=port)