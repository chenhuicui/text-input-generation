# text-input-generation

This repository contains the code used for our paper titled "Large Language Models for Mobile GUI Text Input Generation: An Empirical Study" submitted to IEEE Transactions on Software Engineering (TSE). It includes two directories:
1. `app-collection`: Contains code for collecting Android applications.
2. `text-input-generation`: Contains code for generating text inputs using LLMs.

First, the `app-collection` directory provides four script files:

(1) `google-play-clawer.py`: Crawls application names from Google Play Store.  
(2) `detail-clawer.py`: Retrieves application details such as package names and version numbers.  
(3) Java files under `src/main/java/mo/must/process`: Download APK files of applications.  
(4) `auto_filter_edittext.py`: Filters applications containing text-input components.

After obtaining the APK files, run the code in the `text-generation` directory to generate text inputs for pages.  
The `text-generation` directory contains two subdirectories:  
- `configs`: Defines configuration files:  
  - APK configuration files (under `apk_config/`) define test settings for each app. Each app requires a configuration file named `<app_id>.yaml` (e.g., `com.spotify.music.yaml`). The YAML file includes:  
    (1) `app_name`: Name of the installed app.  
    (2) `delay_detect`: Determines if a page is reached and whether text appears.  
    (3) `navigation_steps`: Reopens the app and navigates to the target page through actions. Supported methods:  
      - Coordinate click:  
        ```yaml
        - action: click / long_click / double_click
          type: coordinate
          raw_x_hex: "000040cc"
          raw_y_hex: "00000ba6"
        ```
        (* Obtain click coordinates via: `adb -s emulator-5556 shell getevent -l`)  
      - Text click:  
        ```yaml
        - action: click / long_click / double_click
          type: text
          target: "Exact text to click"  # Case-sensitive, full match required
        ```
      - Coordinate swipe:  
        ```yaml
        - action: swipe
          raw_fx_hex: "00006d32" # Start X
          raw_fy_hex: "00005b61" # Start Y
          raw_tx_hex: "0000197f" # End X
          raw_ty_hex: "00005b61" # End Y
        ```
    (4) `verify_action`: Additional verification actions, including:  
      - Simulate Enter key:  
        ```yaml
        - action: click
          type: enter
          delay: 2  # Optional delay
        ```
    (5) `verify_disappear` and `verify_appear` (both must be satisfied, but one can be omitted):  
      - `verify_disappear`: Verifies text elements disappearing from the current page:  
        ```yaml
        verify_disappear:
          targets:
            - "Search for your favorite artists..."
            - "xxxxx"
          by: text
        ```
      - `verify_appear`: Verifies text elements appearing on the new page.  

  - `db_config.yaml`: Database configuration:  
    ```yaml
    mysql:
      host: ""
      port: 
      user: ""
      password: ""
      database: ""
      pool_size: 5
      pool_name: ""
    ```
  - `install_config.yaml`: Installation settings:  
    ```yaml
    adb_path: "your/path/to/adb"
    aapt_path: "your/path/to/aapt"
    max_workers: 3
    source: "your/path/to/apk"
    log_config:
      log_dir: "logs"
      log_file: "execution.log"
      log_level: "DEBUG"
    ```
  - `prompt_templates.yaml`: Defines prompts for text input generation.  
  (* All YAML files are loaded via `src/text-generation/src/utils/yaml_utils.py`)

- `src`: Code for generating text inputs:  
  - `apk_management`: Manages APK installation/launch (`installer.py`, `launcher.py`).  
  - `context_extraction`: Extracts context from running APKs (`context_extractor.py`).  
  - `llm_interaction`: Interfaces with LLMs (`prompt_generator.py`, `llm_chatter.py`, `text_input_extractor.py`).  
  - `test_execution`: Executes tests (`action_executor.py`).  
  - `utils`: Helper classes (`yaml_utils.py`, `logger.py`, `db_utils.py`, `assert_utils.py`, `str_utils.py`, `uiautomator_utils.py`).  

`main.py` is the entry point:  
1. **Installs APKs**:  
   - Runs APK installation logic. Stop automatically after installation, then manually explore text-input components.  
2. **Manual Input Field Discovery**:  
   - Find one page with input fields per app.  
3. **Write YAML**:  
   - Create YAML configuration in `configs/apk_config/` following the rules above.  
4. **Execute Script**:  
   - Rerun `main.py` and verify if results match the YAML definitions.  
5. **Check Output**:  
   - Verify if records are updated in the database (implementation uses either DB or file operations).  

`requirements.txt` contains required dependencies.