import statsapi
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
from datetime import datetime, timedelta
import pytz # For timezone conversion
from github import Github
from github.GithubException import UnknownObjectException
import os

# --- CONFIGURATION ---
# GitHub Configuration
GITHUB_TOKEN = os.environ.get("GITHUB_PAT") # Or replace with your PAT: "your_github_personal_access_token"
# IMPORTANT: For security, it's best to use an environment variable for your PAT
# To run locally: export GITHUB_PAT="your_token_here"
GITHUB_REPO_OWNER = "kevdog114"  # Replace with your GitHub username
GITHUB_REPO_NAME = "trmnl_stlcards"    # Replace with your repository name
IMAGE_PATH_IN_REPO = "trmnl_images/cardinals_schedule.png" # Path where the image will be saved in the repo
COMMIT_MESSAGE = "Update St. Louis Cardinals TRMNL schedule image"


# MLB Configuration
TEAM_NAME = "St. Louis Cardinals"
TEAM_ID = 138 # St. Louis Cardinals
DAYS_AHEAD = 4 # Number of days of upcoming games to fetch
DISPLAY_TIMEZONE = "America/Chicago" # Central Time

# Image Configuration
IMAGE_WIDTH = 800
IMAGE_HEIGHT = 480
BACKGROUND_COLOR = "white"
TEXT_COLOR = "black"
try:
    # Try to use a common system font, provide a path if it's not found
    FONT_PATH_REGULAR = "arial.ttf" # Ensure this font is available or provide a full path
    FONT_PATH_BOLD = "arialbd.ttf"  # Ensure this bold font is available
    FONT_SIZE_LARGE = 30
    FONT_SIZE_MEDIUM = 22
    FONT_SIZE_SMALL = 18
except IOError:
    print(f"Font file {FONT_PATH_REGULAR} or {FONT_PATH_BOLD} not found. Please provide a valid .ttf font path.")
    # Fallback to a default font if Pillow can find one (might not be ideal)
    FONT_PATH_REGULAR = None 
    FONT_PATH_BOLD = None


LOGO_URL = "https://a.espncdn.com/i/teamlogos/mlb/500/stl.png" # Cardinals logo (will be converted to B&W)
LOGO_SIZE = (120, 120) # Desired size for the logo on the image

# --- HELPER FUNCTIONS ---

def get_team_logo(url, size):
    """Downloads a logo, converts to B&W, and resizes."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        logo_img = Image.open(BytesIO(response.content))
        # Convert to RGBA first to handle transparency, then to B&W
        logo_img = logo_img.convert("RGBA")
        
        # Create a white background image
        bg = Image.new("RGBA", logo_img.size, (255,255,255,255))
        # Composite the logo onto the white background
        logo_on_bg = Image.alpha_composite(bg, logo_img)

        logo_on_bg = logo_on_bg.convert("L") # Grayscale
        logo_on_bg = logo_on_bg.resize(size, Image.Resampling.LANCZOS)
        # Dither to 1-bit black and white for e-ink
        logo_bw = logo_on_bg.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        return logo_bw
    except requests.RequestException as e:
        print(f"Error downloading logo: {e}")
    except Exception as e:
        print(f"Error processing logo: {e}")
    return None

def format_game_time(game_date_utc_str, target_tz_str):
    """Converts UTC game time string to a user-friendly format in the target timezone."""
    try:
        utc_tz = pytz.utc
        target_tz = pytz.timezone(target_tz_str)
        
        # Handle potential 'Z' at the end of the timestamp
        if game_date_utc_str.endswith('Z'):
            game_date_utc_str = game_date_utc_str[:-1]

        # Try multiple datetime formats that MLB API might use
        dt_utc = None
        formats_to_try = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"]
        for fmt in formats_to_try:
            try:
                dt_utc = datetime.strptime(game_date_utc_str, fmt)
                break
            except ValueError:
                continue
        
        if dt_utc is None:
            print(f"Could not parse game date: {game_date_utc_str}")
            return "Time TBD"

        dt_utc = utc_tz.localize(dt_utc)
        dt_target = dt_utc.astimezone(target_tz)
        return dt_target.strftime("%a %b %d, %-I:%M %p %Z") # Mon Jan 01, 7:00 PM CST
    except Exception as e:
        print(f"Error formatting game time for {game_date_utc_str}: {e}")
        return "Time TBD"

def get_simplified_broadcasts(broadcasts_list):
    """Extracts key TV broadcast information."""
    if not broadcasts_list:
        return "TV TBD"
    
    tv_channels = []
    has_mlbtv = False
    national_tv = set()
    regional_tv = set()

    for broadcast in broadcasts_list:
        b_type = broadcast.get('type', '').upper()
        name = broadcast.get('name', '')
        language = broadcast.get('language', '')
        
        if b_type == "TV" and language == "en": # Only English TV broadcasts
            if "MLB.TV" in name:
                has_mlbtv = True
            elif broadcast.get('isNational'):
                national_tv.add(name)
            else: # Regional
                # Prioritize team-specific RSNs if available, otherwise use generic name
                call_sign = broadcast.get('callSign', '')
                if call_sign and call_sign not in name: # e.g. BSMW
                     regional_tv.add(call_sign)
                else:
                     regional_tv.add(name)


    if national_tv:
        tv_channels.extend(sorted(list(national_tv)))
    if regional_tv:
        tv_channels.extend(sorted(list(regional_tv)))
    
    # Limit the number of displayed regional channels to avoid clutter
    if len(tv_channels) > 2 and regional_tv: # If many channels, prioritize national then some regional
        display_channels = sorted(list(national_tv))
        needed = 2 - len(display_channels)
        if needed > 0:
            display_channels.extend(list(regional_tv)[:needed])
        tv_channels = display_channels


    if not tv_channels and has_mlbtv:
        return "MLB.TV"
    elif tv_channels:
        result = ", ".join(tv_channels)
        if has_mlbtv:
            result += " / MLB.TV"
        # Truncate if too long
        max_len = 35
        if len(result) > max_len:
            result = result[:max_len-3] + "..."
        return result
    elif has_mlbtv: # Should be caught by the first condition, but just in case
        return "MLB.TV"
        
    return "TV TBD"


# --- FETCH MLB DATA ---
def fetch_cardinals_data(team_id, num_days):
    """Fetches upcoming games and current standings for the Cardinals."""
    games_info = []
    standings_info = {"record": "N/A", "rank": "N/A", "gb": "N/A"}
    
    # Fetch Upcoming Games
    start_date = datetime.now().strftime('%Y-%m-%d')
    end_date = (datetime.now() + timedelta(days=num_days)).strftime('%Y-%m-%d')
    
    print(f"Fetching schedule for Cardinals (ID: {team_id}) from {start_date} to {end_date}...")
    try:
        # First, get gamePks from the schedule
        sched = statsapi.schedule(start_date=start_date, end_date=end_date, team=team_id, sportId=1)
        
        for game_data in sched:
            game_pk = game_data['game_id']
            game_datetime_utc = game_data['game_datetime'] # This is usually UTC

            # Fetch detailed game data including broadcasts using gamePk
            # The hydrate parameter is key here.
            detailed_game = statsapi.get('game', {'gamePk': game_pk, 'hydrate': 'broadcasts(all),team,linescore'})
            
            if not detailed_game or 'gameData' not in detailed_game:
                print(f"Could not get detailed data for gamePk {game_pk}")
                continue

            game_status = detailed_game['gameData']['status']['abstractGameState']
            if game_status in ["Final", "Game Over", "Completed Early"]: # Skip past games
                continue

            # Determine home/away and opponent
            home_team_id = detailed_game['gameData']['teams']['home']['id']
            away_team_id = detailed_game['gameData']['teams']['away']['id']
            
            opponent_name = ""
            if home_team_id == team_id:
                opponent_name = f"vs {detailed_game['gameData']['teams']['away']['teamName']}"
            else:
                opponent_name = f"@ {detailed_game['gameData']['teams']['home']['teamName']}"

            # Broadcasts
            api_broadcasts = detailed_game.get('liveData', {}).get('broadcasts', []) # Check liveData first
            if not api_broadcasts: # Fallback if not in liveData (might be in gameData for some API versions/states)
                 api_broadcasts = detailed_game.get('gameData', {}).get('broadcasts', [])


            broadcast_str = get_simplified_broadcasts(api_broadcasts)
            
            # Game time
            formatted_time = format_game_time(detailed_game['gameData']['datetime']['dateTime'], DISPLAY_TIMEZONE)

            games_info.append({
                "opponent": opponent_name,
                "datetime": formatted_time,
                "broadcast": broadcast_str,
                "status": game_status
            })
            if len(games_info) >= num_days + 1: # Fetch a bit more just in case of PPDs, limit to roughly num_days
                break
        
        games_info = games_info[:num_days] # Ensure we only take the desired number of upcoming games

    except Exception as e:
        print(f"Error fetching game schedule: {e}")

    # Fetch Standings
    print("Fetching standings...")
    try:
        # League ID 104 is National League. You might need to adjust if you want overall MLB.
        # The statsapi.standings_data can be complex to parse for a specific team.
        # It's often easier to get all standings and then find your team.
        standings_raw = statsapi.standings_data(leagueId="103,104", division="all", include_wildcard=True, season=datetime.now().year)
        
        found_team = False
        for league_id, league_data in standings_raw.items():
            if found_team: break
            for division_id, division_data in league_data.get('divisions', {}).items():
                if found_team: break
                if 'teams' in division_data:
                    for team_standing in division_data['teams']:
                        if team_standing['team_id'] == team_id:
                            wins = team_standing.get('w', 0)
                            losses = team_standing.get('l', 0)
                            standings_info["record"] = f"{wins}-{losses}"
                            standings_info["rank"] = f"{team_standing.get('div_rank', 'N/A')} in {division_data.get('name_short', 'N/A')}"
                            standings_info["gb"] = f"{team_standing.get('gb', 'N/A')} GB"
                            found_team = True
                            break
        if not found_team:
             print(f"Could not find Cardinals in standings data. Structure might have changed or team not found.")

    except Exception as e:
        print(f"Error fetching standings: {e}")
        
    return games_info, standings_info


# --- CREATE IMAGE ---
def create_schedule_image(games, standings, logo_obj, output_filename="cardinals_schedule.png"):
    """Creates the e-ink image with schedule and standings."""
    img = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), BACKGROUND_COLOR) # Start with RGB for easier drawing
    draw = ImageDraw.Draw(img)

    # Load Fonts
    try:
        font_large = ImageFont.truetype(FONT_PATH_BOLD, FONT_SIZE_LARGE) if FONT_PATH_BOLD else ImageFont.load_default()
        font_medium = ImageFont.truetype(FONT_PATH_REGULAR, FONT_SIZE_MEDIUM) if FONT_PATH_REGULAR else ImageFont.load_default()
        font_small = ImageFont.truetype(FONT_PATH_REGULAR, FONT_SIZE_SMALL) if FONT_PATH_REGULAR else ImageFont.load_default()
    except IOError:
        print("Defaulting to Pillow's load_default() font as specified font was not loaded.")
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()
        
    # --- Left Pane (Logo and Standings) ---
    left_pane_width = LOGO_SIZE[0] + 40 # Logo width + padding
    
    # Draw Logo
    if logo_obj:
        img.paste(logo_obj, (20, 20)) # Paste B&W logo

    # Draw Standings (Below Logo)
    y_pos = LOGO_SIZE[1] + 40 # Start below logo
    draw.text((20, y_pos), "Standings:", font=font_medium, fill=TEXT_COLOR)
    y_pos += 35
    draw.text((20, y_pos), standings.get("record", "N/A"), font=font_medium, fill=TEXT_COLOR)
    y_pos += 30
    draw.text((20, y_pos), standings.get("rank", "N/A"), font=font_small, fill=TEXT_COLOR)
    y_pos += 25
    draw.text((20, y_pos), standings.get("gb", "N/A"), font=font_small, fill=TEXT_COLOR)

    # --- Right Pane (Upcoming Games) ---
    right_pane_x_start = left_pane_width + 20
    y_pos = 20
    
    draw.text((right_pane_x_start, y_pos), "Upcoming Games:", font=font_large, fill=TEXT_COLOR)
    y_pos += FONT_SIZE_LARGE + 15

    if not games:
        draw.text((right_pane_x_start, y_pos), "No upcoming games found.", font=font_medium, fill=TEXT_COLOR)
    else:
        for i, game in enumerate(games):
            if i >= 5 : break # Limit to 5 games on display to prevent clutter
            
            opponent_text = game.get("opponent", "N/A")
            datetime_text = game.get("datetime", "N/A")
            broadcast_text = f"TV: {game.get('broadcast', 'N/A')}"

            draw.text((right_pane_x_start, y_pos), opponent_text, font=font_medium, fill=TEXT_COLOR)
            y_pos += FONT_SIZE_MEDIUM + 5
            draw.text((right_pane_x_start + 10, y_pos), datetime_text, font=font_small, fill=TEXT_COLOR)
            y_pos += FONT_SIZE_SMALL + 5
            draw.text((right_pane_x_start + 10, y_pos), broadcast_text, font=font_small, fill=TEXT_COLOR)
            y_pos += FONT_SIZE_SMALL + 15 # Extra spacing between games

            if y_pos > IMAGE_HEIGHT - FONT_SIZE_SMALL - 10: # Stop if running out of space
                break
                
    # Convert final image to 1-bit black and white for e-ink
    eink_image = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    eink_image.save(output_filename)
    print(f"Image saved as {output_filename}")


# --- UPLOAD TO GITHUB ---
def upload_to_github(token, repo_owner, repo_name, file_path_in_repo, local_file_path, commit_msg):
    """Uploads the generated image to the specified GitHub repository."""
    if not token:
        print("GitHub token not provided. Skipping upload.")
        return
    if not repo_owner or not repo_name:
        print("GitHub repository owner or name not provided. Skipping upload.")
        return

    try:
        g = Github(token)
        user = g.get_user(repo_owner)
        repo = user.get_repo(repo_name)
    except Exception as e:
        print(f"Error accessing GitHub repository {repo_owner}/{repo_name}: {e}")
        return

    try:
        with open(local_file_path, "rb") as f:
            content = f.read()

        try:
            # Check if file exists to update it
            existing_file = repo.get_contents(file_path_in_repo)
            repo.update_file(
                path=file_path_in_repo,
                message=commit_msg,
                content=content,
                sha=existing_file.sha
            )
            print(f"Successfully updated {file_path_in_repo} in {repo_owner}/{repo_name}")
        except UnknownObjectException:
            # File does not exist, create it
            repo.create_file(
                path=file_path_in_repo,
                message=commit_msg,
                content=content
            )
            print(f"Successfully created {file_path_in_repo} in {repo_owner}/{repo_name}")
    except FileNotFoundError:
        print(f"Local image file {local_file_path} not found. Skipping upload.")
    except Exception as e:
        print(f"Error uploading file to GitHub: {e}")


# --- MAIN EXECUTION ---
if __name__ == "__main__":
    if not GITHUB_TOKEN or GITHUB_REPO_OWNER == "YOUR_GITHUB_USERNAME" or GITHUB_REPO_NAME == "YOUR_REPOSITORY_NAME":
        print("WARNING: GitHub credentials are not fully set. Upload will be skipped unless configured.")

    print("Starting St. Louis Cardinals schedule image generation...")
    
    # 1. Fetch Data
    upcoming_games, current_standings = fetch_cardinals_data(TEAM_ID, DAYS_AHEAD)
    
    print("\nFetched Games:")
    if upcoming_games:
        for game in upcoming_games:
            print(f"- {game['opponent']} on {game['datetime']} (TV: {game['broadcast']})")
    else:
        print("No upcoming games data fetched.")
        
    print("\nCurrent Standings:")
    print(f"- Record: {current_standings['record']}")
    print(f"- Rank: {current_standings['rank']}")
    print(f"- GB: {current_standings['gb']}")

    # 2. Get Logo
    cardinals_logo = get_team_logo(LOGO_URL, LOGO_SIZE)
    if not cardinals_logo:
        print("Could not load logo. Proceeding without it.")

    # 3. Create Image
    local_image_filename = "cardinals_schedule_eink.png"
    create_schedule_image(upcoming_games, current_standings, cardinals_logo, local_image_filename)

    # 4. Upload to GitHub
    if os.path.exists(local_image_filename):
         if GITHUB_TOKEN and GITHUB_REPO_OWNER != "YOUR_GITHUB_USERNAME" and GITHUB_REPO_NAME != "YOUR_REPOSITORY_NAME":
            print(f"\nUploading {local_image_filename} to GitHub...")
            upload_to_github(
                GITHUB_TOKEN,
                GITHUB_REPO_OWNER,
                GITHUB_REPO_NAME,
                IMAGE_PATH_IN_REPO,
                local_image_filename,
                COMMIT_MESSAGE
            )
         else:
            print("\nSkipping GitHub upload due to missing configuration (Token, Owner, or Repo Name).")
    else:
        print(f"\nLocal image file {local_image_filename} was not created. Skipping GitHub upload.")
        
    print("\nScript finished.")
