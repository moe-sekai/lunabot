# LunaBot

A multi-functional chatbot based on Nonebot2

Note: This project is for reference and learning purposes only, and is **not** a completely deployable application.

- There may be issues with the deployment steps.

- Missing configurations and data will not be provided.

### Deployment Steps

#### 1. Install Dependencies

- Install dependencies using `pip install -r requirements.txt` (Python >=3.10 is recommended)

- Install playwright browsers by running the command: ```playwright install```

- Install system emoji fonts if emojis fail to render for commands such as `/help`

- Replace the line in `{your_python_dir}/site-packages/pilmoji/helpers.py`:

    ```python
    language_pack: Dict[str, str] = unicode_codes.get_emoji_unicode_dict('en')
    ```

    with
    
    ```python
    import emoji
    language_pack: Dict[str, str] = {data['en']: emj for emj, data in emoji.EMOJI_DATA.items() if 'en' in data and data['status'] <= emoji.STATUS['fully_qualified']}
    ```

#### 2. Setup Configurations

- Copy the configuration from the `example_config` directory to the `config` directory and fill in the missing content as needed.

- Rename `.env.example` to `.env`.

- Find and place the missing data yourself


#### 3. Run the Bot

- Start the project using nonebot2 cli command: `nb run`.

- Send a message `@yourbot /enable` to enable the bot in the group.

- (Optional) Start the Sekai Deck Recommendation Service: [README.md](./src/services/deck_recommender/README.md)


