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
GITHUB_TOKEN = os.environ.get("GITHUB_PAT")

github_repository_env = os.environ.get("GITHUB_REPOSITORY") # Format: 'owner/repo'
if github_repository_env:
    parts = github_repository_env.split('/')
    if len(parts) == 2:
        GITHUB_REPO_OWNER_ENV = parts[0]
        GITHUB_REPO_NAME_ENV = parts[1]
        print(f"Detected GitHub Actions environment: Owner='{GITHUB_REPO_OWNER_ENV}', Repo='{GITHUB_REPO_NAME_ENV}'")
    else:
        GITHUB_REPO_OWNER_ENV = None
        GITHUB_REPO_NAME_ENV = None
        print(f"Warning: GITHUB_REPOSITORY environment variable format is unexpected: {github_repository_env}")
else:
    GITHUB_REPO_OWNER_ENV = None
    GITHUB_REPO_NAME_ENV = None
    print("Not running in a GitHub Actions environment or GITHUB_REPOSITORY not set.")

GITHUB_REPO_OWNER_HARDCODED = "YOUR_GITHUB_USERNAME"
GITHUB_REPO_NAME_HARDCODED = "YOUR_REPOSITORY_NAME"

if GITHUB_REPO_OWNER_HARDCODED == "YOUR_GITHUB_USERNAME" and GITHUB_REPO_OWNER_ENV:
    GITHUB_REPO_OWNER = GITHUB_REPO_OWNER_ENV
else:
    GITHUB_REPO_OWNER = GITHUB_REPO_OWNER_HARDCODED

if GITHUB_REPO_NAME_HARDCODED == "YOUR_REPOSITORY_NAME" and GITHUB_REPO_NAME_ENV:
    GITHUB_REPO_NAME = GITHUB_REPO_NAME_ENV
else:
    GITHUB_REPO_NAME = GITHUB_REPO_NAME_HARDCODED

IMAGE_PATH_IN_REPO = "trmnl_images/cardinals_schedule.png"
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
    FONT_PATH_REGULAR = "arial.ttf"
    FONT_PATH_BOLD = "arialbd.ttf"
    FONT_SIZE_LARGE = 30
    FONT_SIZE_MEDIUM = 22
    FONT_SIZE_SMALL = 18
except IOError:
    print(f"Font file {FONT_PATH_REGULAR} or {FONT_PATH_BOLD} not found. Please provide a valid .ttf font path.")
    FONT_PATH_REGULAR = None
    FONT_PATH_BOLD = None

LOGO_URL = "https://a.espncdn.com/i/teamlogos/mlb/500/stl.png"
LOGO_SIZE = (120, 120)

# --- HELPER FUNCTIONS ---
def get_team_logo(url, size):
    """Downloads a logo, converts to B&W, and resizes."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        logo_img = Image.open(BytesIO(response.content))
        logo_img = logo_img.convert("RGBA")
        bg = Image.new("RGBA", logo_img.size, (255,255,255,255))
        logo_on_bg = Image.alpha_composite(bg, logo_img)
        logo_on_bg = logo_on_bg.convert("L")
        logo_on_bg = logo_on_bg.resize(size, Image.Resampling.LANCZOS)
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
        if game_date_utc_str.endswith('Z'):
            game_date_utc_str = game_date_utc_str[:-1]
        dt_utc = None
        # More robust parsing for various possible datetime string formats from the API
        formats_to_try = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"]
        for fmt in formats_to_try:
            try:
                dt_utc = datetime.strptime(game_date_utc_str, fmt)
                if fmt == "%Y-%m-%d": # API provided only date, time is likely TBD
                     return dt_utc.strftime("%a %b %d (Time TBD)")
                break
            except ValueError:
                continue
        if dt_utc is None:
            # Try to parse just the date part if full datetime fails
            try:
                dt_utc_date_only = datetime.strptime(game_date_utc_str.split('T')[0], "%Y-%m-%d")
                return dt_utc_date_only.strftime("%a %b %d (Time TBD)")
            except ValueError:
                print(f"Could not parse game date: {game_date_utc_str}")
                return "Time TBD"

        dt_utc = utc_tz.localize(dt_utc)
        dt_target = dt_utc.astimezone(target_tz)
        return dt_target.strftime("%a %b %d, %-I:%M %p %Z")
    except Exception as e:
        print(f"Error formatting game time for {game_date_utc_str}: {e}")
        return "Time TBD"

def get_simplified_broadcasts(game_data_item):
    """Extracts key TV broadcast information from a hydrated game object.
       Checks both game_data.broadcasts and game_data.content.media.epg
    """
    all_broadcast_items = []
    
    # Check direct 'broadcasts' array
    if 'broadcasts' in game_data_item and isinstance(game_data_item['broadcasts'], list):
        all_broadcast_items.extend(game_data_item['broadcasts'])
        
    # Check nested 'content.media.epg'
    if ('content' in game_data_item and 
        'media' in game_data_item['content'] and 
        'epg' in game_data_item['content']['media'] and 
        isinstance(game_data_item['content']['media']['epg'], list)):
        for epg_group in game_data_item['content']['media']['epg']:
            if epg_group.get('title', '').upper() in ["MLBTV", "TV"] and isinstance(epg_group.get('items'), list):
                all_broadcast_items.extend(epg_group['items'])

    if not all_broadcast_items:
        return "TV TBD"

    tv_channels = []
    has_mlbtv_type = False # Specifically for MLB.TV streams
    national_tv = set()
    regional_tv = set()

    for broadcast in all_broadcast_items:
        b_type = broadcast.get('type', '').upper() # e.g., TV, HOME, AWAY, MLBTV
        name = broadcast.get('name', broadcast.get('description', '')) # 'name' or 'description'
        #media_state = broadcast.get('mediaState', '') # Helpful for MLB.TV items
        
        # Consider it a TV broadcast if type is TV, or if it's a known RSN/National name
        is_tv_broadcast = (b_type == "TV")

        if "MLB.TV" in name or b_type == "MLBTV":
            has_mlbtv_type = True
            continue # Handle MLB.TV separately at the end

        # Check for national broadcasts (often have 'isNational' or specific names)
        # The 'broadcasts' array items often have 'isNational', 'content.media.epg' items might not.
        if broadcast.get('isNational') or name in ["ESPN", "FOX", "FS1", "TBS", "Apple TV+", "Peacock", "MLB Network"]:
            if name: national_tv.add(name)
        elif is_tv_broadcast or name: # Assume regional if it's TV and not explicitly national
            # Use callSign for regional if available and distinct, otherwise name
            call_sign = broadcast.get('callSign', '')
            if call_sign and call_sign not in name and len(call_sign) < 6: # Short call signs are usually RSNs
                regional_tv.add(call_sign)
            elif name:
                regional_tv.add(name)
    
    # Build the display string
    display_list = []
    if national_tv:
        display_list.extend(sorted(list(national_tv)))
    
    # Add regional channels if space allows (e.g., up to 2-3 total channels shown)
    if regional_tv and len(display_list) < 2:
        display_list.extend(sorted(list(regional_tv))[:2-len(display_list)])

    final_display_string = ", ".join(display_list)

    if has_mlbtv_type:
        if final_display_string:
            # Avoid "MLB Network / MLB.TV" if MLB Network is already listed
            if not ("MLB Network" in final_display_string and "MLB.TV" in "MLB.TV"): # Simplified check
                 final_display_string += " / MLB.TV"
        else:
            final_display_string = "MLB.TV"
            
    if not final_display_string:
        return "TV TBD"

    # Truncate if too long
    max_len = 35
    if len(final_display_string) > max_len:
        final_display_string = final_display_string[:max_len-3] + "..."
    return final_display_string


# --- FETCH MLB DATA ---
def fetch_cardinals_data(team_id, num_days):
    """Fetches upcoming games and current standings for the Cardinals
    using statsapi.get('schedule', ...) for a single API call with hydration.
    """
    games_info = []
    standings_info = {"record": "N/A", "rank": "N/A", "gb": "N/A"}
    
    start_date_dt = datetime.now()
    end_date_dt = start_date_dt + timedelta(days=num_days -1) # -1 because we fetch for num_days inclusive of start

    start_date_str = start_date_dt.strftime('%Y-%m-%d')
    end_date_str = end_date_dt.strftime('%Y-%m-%d') # For multi-day schedule fetch
    
    print(f"Fetching hydrated schedule for Cardinals (ID: {team_id}) from {start_date_str} to {end_date_str} using statsapi.get()...")
    
    try:
        params = {
            'sportId': 1,
            'teamId': team_id,
            'startDate': start_date_str,
            'endDate': end_date_str,
            'hydrate': 'team,broadcasts(all),linescore,game(content(media(epg))),series(content)'
            # 'game(content(media(epg)))' is important for some broadcast info
            # 'series(content)' might provide context but not strictly needed for games
        }
        schedule_response = statsapi.get('schedule', params)

        if not schedule_response or 'dates' not in schedule_response:
            print("No schedule data returned or unexpected format.")
            return games_info, standings_info

        processed_games_count = 0
        for date_obj in schedule_response.get('dates', []):
            for game_data in date_obj.get('games', []):
                if processed_games_count >= num_days:
                    break 

                # Ensure this game involves the target team_id, as schedule might return other games on that day too
                # if not (game_data['teams']['home']['team']['id'] == team_id or \
                #         game_data['teams']['away']['team']['id'] == team_id):
                #     continue
                # The teamId parameter in the API call should already filter this, but double check if needed.

                game_status = game_data.get('status', {}).get('abstractGameState', 'Unknown')
                if game_status in ["Final", "Game Over", "Completed Early", "Cancelled"]:
                    continue

                # Opponent
                home_team_data = game_data.get('teams', {}).get('home', {}).get('team', {})
                away_team_data = game_data.get('teams', {}).get('away', {}).get('team', {})
                
                opponent_name = "vs Unknown"
                if home_team_data.get('id') == team_id:
                    opponent_name = f"vs {away_team_data.get('name', 'Opponent')}"
                elif away_team_data.get('id') == team_id:
                    opponent_name = f"@ {home_team_data.get('name', 'Opponent')}"
                else: # Should not happen if teamId filter works
                    continue 

                # Broadcasts - use the helper that checks multiple locations
                broadcast_str = get_simplified_broadcasts(game_data)
                
                # Game time
                game_datetime_utc_str = game_data.get('gameDate') # This is usually in '2024-05-20T23:10:00Z' format
                if not game_datetime_utc_str: # Fallback if gameDate is missing
                    game_datetime_utc_str = date_obj.get('date') # Use the date of the current iteration
                
                formatted_time = format_game_time(game_datetime_utc_str, DISPLAY_TIMEZONE)

                games_info.append({
                    "opponent": opponent_name,
                    "datetime": formatted_time,
                    "broadcast": broadcast_str,
                    "status": game_status
                })
                processed_games_count += 1
            if processed_games_count >= num_days:
                break
        
    except Exception as e:
        print(f"Error fetching and processing hydrated game schedule with statsapi.get(): {e}")
        import traceback
        traceback.print_exc()


    # Fetch Standings (remains the same)
    print("Fetching standings...")
    try:
        standings_raw = statsapi.standings_data(leagueId="104", division="all", include_wildcard=True, season=datetime.now().year)
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
             print(f"Could not find Cardinals in standings data.")
    except Exception as e:
        print(f"Error fetching standings: {e}")
        
    return games_info, standings_info

# --- CREATE IMAGE (remains largely the same) ---
def create_schedule_image(games, standings, logo_obj, output_filename="cardinals_schedule.png"):
    """Creates the e-ink image with schedule and standings."""
    img = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)
    try:
        font_large = ImageFont.truetype(FONT_PATH_BOLD, FONT_SIZE_LARGE) if FONT_PATH_BOLD else ImageFont.load_default()
        font_medium = ImageFont.truetype(FONT_PATH_REGULAR, FONT_SIZE_MEDIUM) if FONT_PATH_REGULAR else ImageFont.load_default()
        font_small = ImageFont.truetype(FONT_PATH_REGULAR, FONT_SIZE_SMALL) if FONT_PATH_REGULAR else ImageFont.load_default()
    except IOError:
        print("Defaulting to Pillow's load_default() font.")
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    left_pane_width = LOGO_SIZE[0] + 40
    if logo_obj:
        img.paste(logo_obj, (20, 20))
    y_pos = LOGO_SIZE[1] + 40
    draw.text((20, y_pos), "Standings:", font=font_medium, fill=TEXT_COLOR)
    y_pos += 35
    draw.text((20, y_pos), standings.get("record", "N/A"), font=font_medium, fill=TEXT_COLOR)
    y_pos += 30
    draw.text((20, y_pos), standings.get("rank", "N/A"), font=font_small, fill=TEXT_COLOR)
    y_pos += 25
    draw.text((20, y_pos), standings.get("gb", "N/A"), font=font_small, fill=TEXT_COLOR)

    right_pane_x_start = left_pane_width + 20
    y_pos = 20
    draw.text((right_pane_x_start, y_pos), "Upcoming Games:", font=font_large, fill=TEXT_COLOR)
    y_pos += FONT_SIZE_LARGE + 15
    if not games:
        draw.text((right_pane_x_start, y_pos), "No upcoming games found.", font=font_medium, fill=TEXT_COLOR)
    else:
        for i, game in enumerate(games):
            if i >= 5 : break # Limit display to 5 games
            opponent_text = game.get("opponent", "N/A")
            datetime_text = game.get("datetime", "N/A")
            broadcast_text = f"TV: {game.get('broadcast', 'N/A')}"
            draw.text((right_pane_x_start, y_pos), opponent_text, font=font_medium, fill=TEXT_COLOR)
            y_pos += FONT_SIZE_MEDIUM + 5
            draw.text((right_pane_x_start + 10, y_pos), datetime_text, font=font_small, fill=TEXT_COLOR)
            y_pos += FONT_SIZE_SMALL + 5
            draw.text((right_pane_x_start + 10, y_pos), broadcast_text, font=font_small, fill=TEXT_COLOR)
            y_pos += FONT_SIZE_SMALL + 15
            if y_pos > IMAGE_HEIGHT - FONT_SIZE_SMALL - 10:
                break
    eink_image = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    eink_image.save(output_filename)
    print(f"Image saved as {output_filename}")

# --- UPLOAD TO GITHUB (remains the same) ---
def upload_to_github(token, repo_owner, repo_name, file_path_in_repo, local_file_path, commit_msg):
    """Uploads the generated image to the specified GitHub repository."""
    if not token:
        print("GitHub token not provided. Skipping upload.")
        return
    if not repo_owner or not repo_name or repo_owner == "YOUR_GITHUB_USERNAME" or repo_name == "YOUR_REPOSITORY_NAME":
        print("GitHub repository owner or name not properly configured. Skipping upload.")
        return
    try:
        g = Github(token)
        # Correct way to get repo:
        repo = g.get_repo(f"{repo_owner}/{repo_name}")
    except Exception as e:
        print(f"Error accessing GitHub repository {repo_owner}/{repo_name}: {e}")
        return
    try:
        with open(local_file_path, "rb") as f:
            content = f.read()
        try:
            existing_file = repo.get_contents(file_path_in_repo)
            repo.update_file(path=file_path_in_repo, message=commit_msg, content=content, sha=existing_file.sha)
            print(f"Successfully updated {file_path_in_repo} in {repo_owner}/{repo_name}")
        except UnknownObjectException:
            repo.create_file(path=file_path_in_repo, message=commit_msg, content=content)
            print(f"Successfully created {file_path_in_repo} in {repo_owner}/{repo_name}")
    except FileNotFoundError:
        print(f"Local image file {local_file_path} not found. Skipping upload.")
    except Exception as e:
        print(f"Error uploading file to GitHub: {e}")
        import traceback
        traceback.print_exc()


# --- MAIN EXECUTION (remains the same) ---
if __name__ == "__main__":
    if not GITHUB_TOKEN or GITHUB_REPO_OWNER == "YOUR_GITHUB_USERNAME" or GITHUB_REPO_NAME == "YOUR_REPOSITORY_NAME":
        print("WARNING: GitHub credentials are not fully set. Upload will be skipped unless configured.")

    print("Starting St. Louis Cardinals schedule image generation (using statsapi.get for schedule)...")
    upcoming_games, current_standings = fetch_cardinals_data(TEAM_ID, DAYS_AHEAD)
    print("\nFetched Games (Using statsapi.get for schedule):")
    if upcoming_games:
        for game in upcoming_games:
            print(f"- {game['opponent']} on {game['datetime']} (TV: {game['broadcast']})")
    else:
        print("No upcoming games data fetched or error during fetch.")
    print("\nCurrent Standings:")
    print(f"- Record: {current_standings['record']}")
    print(f"- Rank: {current_standings['rank']}")
    print(f"- GB: {current_standings['gb']}")

    cardinals_logo = get_team_logo(LOGO_URL, LOGO_SIZE)
    if not cardinals_logo:
        print("Could not load logo. Proceeding without it.")

    local_image_filename = "cardinals_schedule_eink.png"
    create_schedule_image(upcoming_games, current_standings, cardinals_logo, local_image_filename)

    if os.path.exists(local_image_filename):
         if GITHUB_TOKEN and GITHUB_REPO_OWNER != "YOUR_GITHUB_USERNAME" and GITHUB_REPO_NAME != "YOUR_REPOSITORY_NAME":
            print(f"\nUploading {local_image_filename} to GitHub...")
            upload_to_github(GITHUB_TOKEN, GITHUB_REPO_OWNER, GITHUB_REPO_NAME, IMAGE_PATH_IN_REPO, local_image_filename, COMMIT_MESSAGE)
         else:
            print("\nSkipping GitHub upload due to missing configuration.")
    else:
        print(f"\nLocal image file {local_image_filename} was not created. Skipping GitHub upload.")
    print("\nScript finished.")
