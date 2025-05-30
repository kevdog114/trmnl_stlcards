import statsapi
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
from datetime import datetime, timedelta
import pytz # For timezone conversion
# from github import Github # No longer needed for direct upload from script
# from github.GithubException import UnknownObjectException # No longer needed
import os
import traceback # For more detailed error logging
import json # Added for JSON manipulation

# --- CONFIGURATION ---
# GitHub repository details (used for constructing URLs in JSON, not for direct upload by this script)
# These will be implicitly handled by the GitHub Actions workflow for commits.
GITHUB_REPO_OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", "YOUR_GITHUB_USERNAME") # GITHUB_REPOSITORY_OWNER is auto-set by Actions
GITHUB_REPO_NAME = os.environ.get("GITHUB_REPOSITORY", "YOUR_REPOSITORY_NAME").split('/')[-1] if os.environ.get("GITHUB_REPOSITORY") else "YOUR_REPOSITORY_NAME"


IMAGE_PATH_IN_REPO = "trmnl_images/cardinals_schedule.png" # Path in the repo where image will be
LOCAL_IMAGE_FULL_PATH = IMAGE_PATH_IN_REPO # Python script will save it here locally

JSON_REDIRECT_FILENAME = "trmnl_redirect.json" # Local filename for the JSON
JSON_REDIRECT_PATH_IN_REPO = JSON_REDIRECT_FILENAME # Path in the repo for the JSON (e.g., root for Pages)


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
    FONT_PATH_REGULAR = "LiberationSans-Regular.ttf" 
    FONT_PATH_BOLD = "LiberationSans-Bold.ttf"
    ImageFont.truetype(FONT_PATH_REGULAR, 10) 
    ImageFont.truetype(FONT_PATH_BOLD, 10)
    print(f"Attempting to use Liberation fonts by name: {FONT_PATH_REGULAR}, {FONT_PATH_BOLD}")

    FONT_SIZE_LARGE = 36
    FONT_SIZE_MEDIUM_BOLD = 28
    FONT_SIZE_MEDIUM = 26
    FONT_SIZE_SMALL = 20
    FONT_SIZE_XSMALL = 16
except IOError:
    print(f"Specified Liberation font files not found by name. Trying common full paths or defaulting.")
    try:
        FONT_PATH_REGULAR = "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
        FONT_PATH_BOLD = "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        ImageFont.truetype(FONT_PATH_REGULAR, 10)
        ImageFont.truetype(FONT_PATH_BOLD, 10)
        print(f"Using full paths for Liberation fonts: {FONT_PATH_REGULAR}, {FONT_PATH_BOLD}")
    except IOError:
        print(f"Full paths for Liberation fonts also not found. Defaulting to Pillow's load_default() font.")
        FONT_PATH_REGULAR = None 
        FONT_PATH_BOLD = None    

    FONT_SIZE_LARGE = 32
    FONT_SIZE_MEDIUM_BOLD = 26
    FONT_SIZE_MEDIUM = 24
    FONT_SIZE_SMALL = 18
    FONT_SIZE_XSMALL = 14


LOGO_URL = "https://a.espncdn.com/i/teamlogos/mlb/500/stl.png"
LOGO_SIZE = (130, 130) 

# --- HELPER FUNCTIONS ---
def get_team_logo(url, size):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        logo_img = Image.open(BytesIO(response.content))
        logo_img = logo_img.convert("RGBA")
        bg = Image.new("RGBA", logo_img.size, (255,255,255,255))
        logo_on_bg = Image.alpha_composite(bg, logo_img)
        logo_on_bg = logo_on_bg.convert("L") 
        logo_on_bg = logo_on_bg.resize(size, Image.Resampling.LANCZOS)
        return logo_on_bg 
    except requests.RequestException as e:
        print(f"Error downloading logo: {e}")
    except Exception as e:
        print(f"Error processing logo: {e}")
    return None

def format_game_time(game_date_utc_str, target_tz_str):
    if not isinstance(game_date_utc_str, str): 
        print(f"Warning: format_game_time received non-string input: {game_date_utc_str}")
        return "Time TBD"
    try:
        utc_tz = pytz.utc
        target_tz = pytz.timezone(target_tz_str)
        if game_date_utc_str.endswith('Z'):
            game_date_utc_str = game_date_utc_str[:-1]
        dt_utc = None
        formats_to_try = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"]
        for fmt in formats_to_try:
            try:
                dt_utc = datetime.strptime(game_date_utc_str, fmt)
                if fmt == "%Y-%m-%d":
                     return dt_utc.strftime("%a %b %d (Time TBD)")
                break
            except ValueError:
                continue
        if dt_utc is None:
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
    all_broadcast_items = []
    if 'broadcasts' in game_data_item and isinstance(game_data_item['broadcasts'], list):
        all_broadcast_items.extend(game_data_item['broadcasts'])
    if ('content' in game_data_item and 
        'media' in game_data_item['content'] and 
        'epg' in game_data_item['content']['media'] and 
        isinstance(game_data_item['content']['media']['epg'], list)):
        for epg_group in game_data_item['content']['media']['epg']:
            epg_title_raw = epg_group.get('title', '')
            epg_title = epg_title_raw.upper() if isinstance(epg_title_raw, str) else ''
            if epg_title in ["MLBTV", "TV"] and isinstance(epg_group.get('items'), list):
                all_broadcast_items.extend(epg_group['items'])
    if not all_broadcast_items: return ["TBD"] 
    national_tv = set()
    regional_tv = set()
    for broadcast in all_broadcast_items:
        if not isinstance(broadcast, dict): continue
        b_type_raw = broadcast.get('type', '')
        b_type = b_type_raw.upper() if isinstance(b_type_raw, str) else ''
        if b_type in ["AM", "FM"]: continue
        name_raw = broadcast.get('name', broadcast.get('description', ''))
        name = name_raw if isinstance(name_raw, str) else ''
        if "MLB.TV" in name or b_type == "MLBTV": continue 
        is_tv_broadcast = (b_type == "TV" or (not b_type and name))
        if "FanDuel Sports Network" in name:
            short_name = name.replace("FanDuel Sports Network", "FanDuel").strip()
            if not short_name or short_name == "FanDuel": short_name = "FanDuel SN" 
            elif not short_name.startswith("FanDuel "): short_name = f"FanDuel {short_name}"
            if broadcast.get('isNational'): national_tv.add(short_name)
            else: regional_tv.add(short_name)
            continue 
        if broadcast.get('isNational') or name in ["ESPN", "FOX", "FS1", "TBS", "Apple TV+", "Peacock", "MLB Network"]:
            if name: national_tv.add(name)
        elif is_tv_broadcast or name: 
            call_sign_raw = broadcast.get('callSign', '')
            call_sign = call_sign_raw if isinstance(call_sign_raw, str) else ''
            if call_sign and call_sign not in name and len(call_sign) < 7 and len(call_sign) > 2 : regional_tv.add(call_sign)
            elif name: regional_tv.add(name)
    display_list = []
    if national_tv: display_list.extend(sorted(list(national_tv)))
    if regional_tv:
        allowed_regional_count = max(0, 3 - len(display_list)) 
        if allowed_regional_count > 0: display_list.extend(sorted(list(regional_tv))[:allowed_regional_count])
    if not display_list: return ["TBD"] 
    return display_list

# --- FETCH MLB DATA ---
def fetch_cardinals_data(team_id, num_days):
    games_info = []
    standings_info = {"record": "N/A", "rank": "N/A", "gb": "N/A"}
    start_date_dt = datetime.now()
    end_date_dt = start_date_dt + timedelta(days=num_days -1) 
    start_date_str = start_date_dt.strftime('%Y-%m-%d')
    end_date_str = end_date_dt.strftime('%Y-%m-%d')
    print(f"Fetching hydrated schedule for Cardinals (ID: {team_id}) from {start_date_str} to {end_date_str} using statsapi.get()...")
    try:
        schedule_params = {
            'sportId': 1, 'teamId': team_id, 'startDate': start_date_str, 'endDate': end_date_str,
            'hydrate': 'team,broadcasts(all),linescore,game(content(media(epg))),series(content),venue' 
        }
        schedule_response = statsapi.get('schedule', schedule_params)
        if not schedule_response or 'dates' not in schedule_response:
            print("No schedule data returned or unexpected format from statsapi.get('schedule').")
        else:
            processed_games_count = 0
            for date_obj in schedule_response.get('dates', []):
                for game_data in date_obj.get('games', []): 
                    if processed_games_count >= num_days: break
                    game_status_obj = game_data.get('status', {})
                    game_status = game_status_obj.get('abstractGameState', 'Unknown') if isinstance(game_status_obj, dict) else 'Unknown'
                    if game_status in ["Final", "Game Over", "Completed Early", "Cancelled"]: continue
                    teams_data = game_data.get('teams', {})
                    home_team_data = teams_data.get('home', {}).get('team', {}) if isinstance(teams_data.get('home',{}), dict) else {}
                    away_team_data = teams_data.get('away', {}).get('team', {}) if isinstance(teams_data.get('away',{}), dict) else {}
                    opponent_name_full = "vs Unknown" 
                    game_type = "Unknown" 
                    home_id = home_team_data.get('id')
                    away_id = away_team_data.get('id')
                    if home_id == team_id:
                        opponent_name_full = f"vs {away_team_data.get('name', 'Opponent')}"
                        game_type = "Home"
                    elif away_id == team_id:
                        opponent_name_full = f"@ {home_team_data.get('name', 'Opponent')}"
                        game_type = "Away"
                    else: continue 
                    broadcast_str_list = get_simplified_broadcasts(game_data) 
                    game_datetime_utc_str = game_data.get('gameDate') 
                    if not game_datetime_utc_str and isinstance(date_obj, dict): 
                        game_datetime_utc_str = date_obj.get('date')
                    formatted_time = format_game_time(game_datetime_utc_str, DISPLAY_TIMEZONE)
                    # Stadium name is fetched but not used in image per user request
                    # stadium_name = game_data.get('venue', {}).get('name', 'Stadium TBD') 
                    games_info.append({
                        "opponent_full": opponent_name_full, 
                        "datetime": formatted_time, "broadcast": broadcast_str_list, 
                        "status": game_status, "game_type": game_type 
                        # "stadium": stadium_name # No longer needed for image
                    })
                    processed_games_count += 1
                if processed_games_count >= num_days: break
    except Exception as e:
        print(f"Error in fetch_cardinals_data (schedule part): {e}"); traceback.print_exc()
    print("Fetching standings using statsapi.get('standings')...")
    try:
        current_year = datetime.now().year
        standings_params = {
            'leagueId': "103,104", 'season': str(current_year), 'standingsTypes': 'regularSeason',
        }
        standings_response = statsapi.get('standings', standings_params)
        if not standings_response or 'records' not in standings_response:
            print("Standings data is empty or not in expected format from statsapi.get('standings').")
        else:
            found_team = False
            for record in standings_response.get('records', []):
                if found_team: break
                if not isinstance(record, dict) or 'teamRecords' not in record: continue
                division_name_obj = record.get('division', {})
                division_name = division_name_obj.get('nameShort', 'N/A') if isinstance(division_name_obj, dict) else 'N/A'
                if division_name == 'N/A' and 'league' in record: 
                    league_name_obj = record.get('league',{})
                    division_name = league_name_obj.get('nameShort', 'League') if isinstance(league_name_obj, dict) else 'League'
                for team_standing in record.get('teamRecords', []):
                    if not isinstance(team_standing, dict) or 'team' not in team_standing: continue
                    team_api_data = team_standing.get('team', {})
                    if not isinstance(team_api_data, dict): continue
                    if team_api_data.get('id') == team_id:
                        league_record = team_standing.get('leagueRecord', {})
                        wins = league_record.get('wins', 0) if isinstance(league_record, dict) else 0
                        losses = league_record.get('losses', 0) if isinstance(league_record, dict) else 0
                        standings_info["record"] = f"{wins}-{losses}"
                        rank_val = team_standing.get('divisionRank', team_standing.get('leagueRank', 'N/A'))
                        gb_val = team_standing.get('gamesBack', 'N/A')
                        if gb_val == '-': gb_val = '0.0' 
                        standings_info["rank"] = f"{rank_val} in {division_name}"
                        standings_info["gb"] = f"{gb_val} GB"
                        found_team = True; break
            if not found_team: print(f"Could not find Cardinals (ID: {team_id}) in standings data.")
    except Exception as e:
        print(f"Error fetching standings: {e}"); traceback.print_exc()
    return games_info, standings_info

# --- CREATE IMAGE ---
def create_schedule_image(games, standings, logo_obj, output_image_path):
    img = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)
    font_large, font_medium, font_small, font_small_bold, font_xsmall, font_medium_bold = None, None, None, None, None, None
    try:
        if FONT_PATH_REGULAR and FONT_PATH_BOLD:
            font_large = ImageFont.truetype(FONT_PATH_BOLD, FONT_SIZE_LARGE)
            font_medium_bold = ImageFont.truetype(FONT_PATH_BOLD, FONT_SIZE_MEDIUM_BOLD)
            font_medium = ImageFont.truetype(FONT_PATH_REGULAR, FONT_SIZE_MEDIUM)
            font_small = ImageFont.truetype(FONT_PATH_REGULAR, FONT_SIZE_SMALL)
            font_small_bold = ImageFont.truetype(FONT_PATH_BOLD, FONT_SIZE_SMALL)
            font_xsmall = ImageFont.truetype(FONT_PATH_REGULAR, FONT_SIZE_XSMALL)
            print(f"Successfully loaded fonts by name: {FONT_PATH_BOLD}, {FONT_PATH_REGULAR}")
        else: raise IOError("Font paths were None.")
    except IOError: 
        print("Defaulting to Pillow's load_default() font."); font_large, font_medium_bold, font_medium, font_small, font_small_bold, font_xsmall = [ImageFont.load_default()]*6
    logo_x_padding, logo_y_padding = 20, 20; left_pane_width = LOGO_SIZE[0] + logo_x_padding * 2 
    if logo_obj: img.paste(logo_obj, (logo_x_padding, logo_y_padding), mask=logo_obj if logo_obj.mode == 'RGBA' else None)
    y_pos = logo_y_padding + LOGO_SIZE[1] + 25; standings_x = logo_x_padding
    draw.text((standings_x, y_pos), "Standings:", font=font_medium, fill=TEXT_COLOR); y_pos += FONT_SIZE_MEDIUM + 10 
    draw.text((standings_x, y_pos), standings.get("record", "N/A"), font=font_medium, fill=TEXT_COLOR); y_pos += FONT_SIZE_MEDIUM + 10 
    draw.text((standings_x, y_pos), standings.get("rank", "N/A"), font=font_small, fill=TEXT_COLOR); y_pos += FONT_SIZE_SMALL + 10  
    draw.text((standings_x, y_pos), standings.get("gb", "N/A"), font=font_small, fill=TEXT_COLOR)
    right_pane_x_start = left_pane_width + 25; y_pos = logo_y_padding 
    draw.text((right_pane_x_start, y_pos), "Upcoming Games:", font=font_large, fill=TEXT_COLOR); y_pos += FONT_SIZE_LARGE + 20
    if not games: draw.text((right_pane_x_start, y_pos), "No upcoming games found.", font=font_medium, fill=TEXT_COLOR)
    else:
        games_to_display = 3 # Try to fit 3 games with the new layout
        for i, game in enumerate(games):
            if i >= games_to_display : break 
            opponent_full_text, datetime_text, broadcast_list, game_type_text = game.get("opponent_full", "N/A"), game.get("datetime", "N/A"), game.get("broadcast", ["TBD"]), game.get("game_type", "")
            
            # Construct opponent line with Home/Away status
            opponent_display_text = opponent_full_text
            if game_type_text:
                opponent_display_text += f" ({game_type_text})"

            num_tv_lines = len(broadcast_list) if broadcast_list else 1
            estimated_height = (FONT_SIZE_MEDIUM_BOLD + 8 + # Date/Time
                                FONT_SIZE_MEDIUM + 10 +    # Opponent + Home/Away
                                (FONT_SIZE_SMALL + 5) * num_tv_lines + # TV Lines
                                30) # Overall padding for game block
                                
            if y_pos + estimated_height > IMAGE_HEIGHT - logo_y_padding - FONT_SIZE_XSMALL - 10 : 
                print(f"Not enough vertical space for game {i+1}."); 
                if i < 1: draw.text((right_pane_x_start, y_pos), "Not enough space for game details.", font=font_small, fill=TEXT_COLOR)
                break
            
            draw.text((right_pane_x_start, y_pos), datetime_text, font=font_medium_bold, fill=TEXT_COLOR); y_pos += FONT_SIZE_MEDIUM_BOLD + 8
            
            # Truncate opponent_display_text if necessary
            available_width_for_opponent = IMAGE_WIDTH - (right_pane_x_start + 10) - 10
            current_opponent_display_text = opponent_display_text
            while draw.textlength(current_opponent_display_text, font=font_medium) > available_width_for_opponent and len(current_opponent_display_text) > 10: 
                current_opponent_display_text = current_opponent_display_text[:-4] + "..."
            draw.text((right_pane_x_start + 10, y_pos), current_opponent_display_text, font=font_medium, fill=TEXT_COLOR); y_pos += FONT_SIZE_MEDIUM + 10
            
            if broadcast_list:
                tv_label_y = y_pos; draw.text((right_pane_x_start + 10, tv_label_y), "TV:", font=font_small_bold, fill=TEXT_COLOR)
                channel_x_start = right_pane_x_start + 10 + draw.textlength("TV:  ", font=font_small_bold) 
                current_line_y = tv_label_y
                for k, channel in enumerate(broadcast_list):
                    if k > 0: current_line_y += FONT_SIZE_SMALL + 5
                    if current_line_y > IMAGE_HEIGHT - (FONT_SIZE_SMALL + 5) : break 
                    draw.text((channel_x_start, current_line_y), channel, font=font_small, fill=TEXT_COLOR)
                y_pos = current_line_y + FONT_SIZE_SMALL + 5
            else: draw.text((right_pane_x_start + 10, y_pos), "TV: TBD", font=font_small_bold, fill=TEXT_COLOR); y_pos += FONT_SIZE_SMALL + 5
            y_pos += 25 
    refresh_date_str = f"Data refreshed on {datetime.now().strftime('%m-%d-%Y')}"
    text_width = draw.textlength(refresh_date_str, font=font_xsmall)
    text_x = IMAGE_WIDTH - text_width - 15; text_y = IMAGE_HEIGHT - FONT_SIZE_XSMALL - 15 
    draw.text((text_x, text_y), refresh_date_str, font=font_xsmall, fill=TEXT_COLOR)
    eink_image = img.convert("1", dither=Image.Dither.NONE); 
    os.makedirs(os.path.dirname(output_image_path), exist_ok=True)
    eink_image.save(output_image_path); print(f"Image saved as {output_image_path}")

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("Starting St. Louis Cardinals schedule image generation...")
    upcoming_games, current_standings = fetch_cardinals_data(TEAM_ID, DAYS_AHEAD)
    print("\nFetched Games:")
    if upcoming_games:
        for game in upcoming_games: 
            broadcast_display = "/".join(game['broadcast']) if isinstance(game['broadcast'], list) else game['broadcast']
            # Updated print to show game_type, stadium is no longer in game dict for image
            print(f"- {game.get('opponent_full','N/A')} ({game.get('game_type','?')}) on {game['datetime']} (TV: {broadcast_display})")
    else: print("No upcoming games data fetched.")
    print("\nCurrent Standings:")
    print(f"- Record: {current_standings['record']}")
    print(f"- Rank: {current_standings['rank']}")
    print(f"- GB: {current_standings['gb']}")
    cardinals_logo = get_team_logo(LOGO_URL, LOGO_SIZE)
    if not cardinals_logo: print("Could not load logo. Proceeding without it.")
    create_schedule_image(upcoming_games, current_standings, cardinals_logo, LOCAL_IMAGE_FULL_PATH)
    print(f"\nGenerating {JSON_REDIRECT_FILENAME} content...")
    actual_repo_owner = GITHUB_REPO_OWNER 
    actual_repo_name = GITHUB_REPO_NAME
    default_branch = "main" 
    static_image_url_in_repo = f"https://raw.githubusercontent.com/{actual_repo_owner}/{actual_repo_name}/{default_branch}/{IMAGE_PATH_IN_REPO}"
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    dynamic_filename_for_json_property = f"cardinals_schedule_{timestamp}.png" 
    redirect_json_content = {
        "url": static_image_url_in_repo, 
        "filename": dynamic_filename_for_json_property,
        "refresh_rate": 21600 # Added refresh_rate
    }
    try:
        # Ensure directory for JSON exists if JSON_REDIRECT_FILENAME includes a path
        json_dir = os.path.dirname(JSON_REDIRECT_FILENAME)
        if json_dir and not os.path.exists(json_dir): # Check if json_dir is not empty
            os.makedirs(json_dir, exist_ok=True)
        
        with open(JSON_REDIRECT_FILENAME, 'w') as f:
            json.dump(redirect_json_content, f, indent=2)
        print(f"Successfully saved {JSON_REDIRECT_FILENAME} locally.")
    except Exception as e:
        print(f"Error saving {JSON_REDIRECT_FILENAME} locally: {e}"); traceback.print_exc()
    print("\nScript finished. Image and JSON redirect file are saved locally.")
    print(f"Image at: {LOCAL_IMAGE_FULL_PATH}")
    print(f"JSON at: {JSON_REDIRECT_FILENAME}")
    print("The GitHub Actions workflow will handle committing and deploying these files.")

