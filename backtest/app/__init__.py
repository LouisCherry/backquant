#!/opt/anaconda3/bin/python3
#coding: utf8

from .config import CONFIG


def create_app(config_name):
    print("Starting create_app function...")
    
    print("Importing Flask...")
    from flask import Flask
    print("Importing CORS...")
    from flask_cors import CORS
    
    print("Importing blueprints...")
    from .api.login_api import bp_login
    print("Imported bp_login")
    from .api.backtest_api import bp_backtest
    print("Imported bp_backtest")
    from .api.research_api import bp_research
    print("Imported bp_research")
    from .api.system_api import bp_system
    print("Imported bp_system")
    from .api.market_data_api import bp_market_data
    print("Imported bp_market_data")
    from .api.packages_api import bp_packages
    print("Imported bp_packages")
    
    print("Importing other modules...")
    from .backtest.services.runner import ensure_default_demo_strategy
    print("Imported ensure_default_demo_strategy")
    from .database import get_db_connection, get_db_type
    print("Imported get_db_connection, get_db_type")
    from .market_data.scheduler import init_scheduler
    print("Imported init_scheduler")

    app = Flask(__name__)
    CORS(app, supports_credentials=True)
    app.config.from_object(CONFIG[config_name])

    app.register_blueprint(bp_login)
    app.register_blueprint(bp_backtest)
    app.register_blueprint(bp_research)
    app.register_blueprint(bp_system)
    app.register_blueprint(bp_market_data)
    app.register_blueprint(bp_packages)

    try:
        print("Initializing app context...")
        with app.app_context():
            print("Ensuring default demo strategy...")
            ensure_default_demo_strategy()
            # Initialize database schema before background services start.
            print("Importing database modules...")
            from .market_data.db_init import init_database, init_database_with_connection
            from .market_data.utils import get_market_data_db_path
            from .api.packages_api import refresh_packages_cache
            
            print("Getting market data DB path...")
            db_path = get_market_data_db_path()
            print(f"Market data DB path: {db_path}")
            
            print("Creating DB directory...")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            
            print(f"DB type: {get_db_type()}")
            if get_db_type() == "mariadb":
                print("Using MariaDB...")
                with get_db_connection("market_data") as db:
                    print("Initializing database with connection...")
                    init_database_with_connection(db)
            else:
                print("Using SQLite...")
                print("Initializing database...")
                init_database(db_path)
            
            print("Initializing scheduler...")
            init_scheduler()
            
            # Initialize Python packages cache
            print("Refreshing packages cache...")
            refresh_packages_cache()
            
            print("App initialization completed successfully!")
    except Exception as e:
        print(f"ERROR: Failed to initialize app: {e}")
        import traceback
        traceback.print_exc()
        app.logger.exception("failed to initialize app")

    return app
