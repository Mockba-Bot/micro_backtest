import asyncio
import aiohttp
import httpx
import sys
import os
import pandas as pd
from sklearn.model_selection import train_test_split, RandomizedSearchCV
from sklearn.ensemble import RandomForestClassifier
import pandas_ta as ta
from scipy.stats import randint
from dotenv import load_dotenv
import joblib  # Library for model serialization
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests

import warnings
warnings.filterwarnings("ignore")
# Load environment variables from the .env file
load_dotenv(dotenv_path=".env.micro.backtest")

# Access the environment variables
PATH_OPERATIONS = os.getenv("PATH_OPERATIONS")
MODEL_PATH = os.getenv("MODEL_PATH")
FILES_PATH = os.getenv("FILES_PATH")
CPU_COUNT = os.getenv("CPU_COUNT")
MICRO_CENTRAL_URL = os.getenv("MICRO_CENTRAL_URL")
cpu_count = os.cpu_count()-int(CPU_COUNT)

# Set dynamic model path based on pair and timeframe
def get_model_path(pair, timeframe):
    return f'{MODEL_PATH}/trained_model_{pair}_{timeframe}.pkl'

def get_last_non_zero_crypto(data):
    non_zero_crypto = data[data['crypto'] > 0]['crypto']
    if not non_zero_crypto.empty:
        return non_zero_crypto.iloc[-1]
    else:
        return 0  # or handle it as needed, e.g., None    
# Def get all Binance
import requests

async def get_all_binance(pair, timeframe, token, save=False):
    url = f"{MICRO_CORE_URL}/historical-data"
    payload = {
        "symbol": pair,
        "kline_size": timeframe,
        "token": token,
        "save": save
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status == 200:
                return await response.json()
            else:
                response.raise_for_status()



# Fetch historical data from the database
async def get_historical_data(pair, timeframe, values):
    url = f"{MICRO_CORE_URL}/query-historical-data"
    payload = {
        "pair": pair,
        "timeframe": timeframe,
        "values": values
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status == 200:
                data = await response.json()
                df = pd.DataFrame(data)
                
                # Convert columns to numeric types
                df['close'] = pd.to_numeric(df['close'])
                df['high'] = pd.to_numeric(df['high'])
                df['low'] = pd.to_numeric(df['low'])
                df['volume'] = pd.to_numeric(df['volume'])
                
                return df
            else:
                response.raise_for_status()

# Add technical indicators to the data
def add_indicators(data):
    # Ensure the columns are of numeric type
    data['close'] = pd.to_numeric(data['close'])
    data['high'] = pd.to_numeric(data['high'])
    data['low'] = pd.to_numeric(data['low'])
    data['volume'] = pd.to_numeric(data['volume'])

    # Add technical indicators
    data['rsi'] = ta.rsi(data['close'])
    macd = ta.macd(data['close'])
    data['macd'] = macd.iloc[:, 0]
    data['macd_signal'] = macd.iloc[:, 1]
    data['macd_diff'] = macd.iloc[:, 2]
    bollinger = ta.bbands(data['close'])
    data['bollinger_hband'] = bollinger.iloc[:, 0]
    data['bollinger_mavg'] = bollinger.iloc[:, 1]
    data['bollinger_lband'] = bollinger.iloc[:, 2]
    data['ema'] = ta.ema(data['close'])
    # Average True Range (ATR) - Volatility indicator
    data['ATR'] = ta.atr(data['high'], data['low'], data['close'])

    return data

# Train the machine learning model with advanced hyperparameter tuning
def train_model(data, model_path):
    # Calculate return and target columns
    data['return'] = data['close'].pct_change().shift(-1)
    data['target'] = (data['return'] > 0).astype(int)
    features = ['rsi', 'macd', 'macd_signal', 'macd_diff', 'bollinger_hband', 'bollinger_mavg', 'bollinger_lband', 'ema', 'ATR']

    # Prepare training and testing datasets
    X = data[features].dropna()
    y = data['target'].dropna().loc[X.index]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Define hyperparameter search space
    param_distributions = {
        'n_estimators': randint(100, 500),
        'max_depth': randint(10, 80),
        'min_samples_split': randint(2, 20),
        'min_samples_leaf': randint(1, 20),
        'max_features': ['sqrt', 'log2']
    }
    
    # Perform randomized hyperparameter search
    randomized_search = RandomizedSearchCV(
        RandomForestClassifier(random_state=42), 
        param_distributions, 
        n_iter=300,  # Increased number of iterations
        cv=3, 
        scoring='accuracy', 
        random_state=42,
        n_jobs=cpu_count
    )
    randomized_search.fit(X_train, y_train)
    
    # Get the best model from the search
    best_model = randomized_search.best_estimator_

    # Save the trained model to disk
    joblib.dump(best_model, model_path)
    
    return best_model, features

# Update the existing model with new data
def update_model(existing_model, new_data, features):
    # Calculate return and target columns for the new data
    new_data['return'] = new_data['close'].pct_change().shift(-1)
    new_data['target'] = (new_data['return'] > 0).astype(int)
    
    # Prepare the new data for training
    X_new = new_data[features].dropna()
    y_new = new_data['target'].dropna().loc[X_new.index]
    
    # Update the existing model with the new data
    existing_model.fit(X_new, y_new)
    
    return existing_model

# Backtest the strategy with initial investment, stop-loss, and fees
async def backtest(data, model, features, initial_investment=10000, stop_loss_threshold=0.05, maker_fee=0.001, taker_fee=0.001, gain_threshold=0.001):
    data = data.dropna().copy()
    data['predicted'] = model.predict(data[features])
    data['strategy_return'] = 0.0  # Initialize strategy return

    # Initialize portfolio values
    data['strategy_portfolio_value'] = initial_investment
    data['cash'] = initial_investment
    data['crypto'] = 0
    position_open = False
    last_buy_price = 0  # New variable to store the last buy price
    first_trade = True  # Variable to track the first trade

    # Iterate over the data to calculate the portfolio value
    for i in range(1, len(data)):
        if data['predicted'].iloc[i - 1] == 1:  # Buy signal
            expected_return = (data['close'].iloc[i] - last_buy_price) / last_buy_price if last_buy_price != 0 else 0  # Use last buy price for expected return
            if not position_open or (position_open and first_trade):  # Execute first trade unconditionally
                # Buy crypto with all available cash (apply maker fee)
                crypto_bought = (data['cash'].iloc[i - 1] / data['close'].iloc[i]) * (1 - maker_fee)
                data['crypto'].iloc[i] = crypto_bought
                data['cash'].iloc[i] = 0
                position_open = True
                last_buy_price = data['close'].iloc[i]  # Set buy price
                first_trade = False  # Set first_trade to False after the first trade
            else:
                # Maintain the position
                data['crypto'].iloc[i] = data['crypto'].iloc[i - 1]
                data['cash'].iloc[i] = data['cash'].iloc[i - 1]

        elif data['predicted'].iloc[i - 1] == 0:  # Sell signal
            if position_open:
                # Calculate expected return and loss based on last buy price
                expected_return = (data['close'].iloc[i] - last_buy_price) / last_buy_price  # Return if sold now
                expected_loss = (last_buy_price - data['close'].iloc[i]) / last_buy_price  # Loss if sold now

                # Sell condition: If the expected return is above the gain threshold, or expected loss is too much
                if expected_return > gain_threshold or expected_loss > stop_loss_threshold:
                    # Sell all crypto and hold cash (apply taker fee)
                    cash_from_sale = data['crypto'].iloc[i - 1] * data['close'].iloc[i] * (1 - taker_fee)
                    data['cash'].iloc[i] = cash_from_sale
                    data['crypto'].iloc[i] = 0
                    position_open = False
                else:
                    # Maintain the position
                    data['crypto'].iloc[i] = data['crypto'].iloc[i - 1]
                    data['cash'].iloc[i] = data['cash'].iloc[i - 1]
            else:
                # Maintain the position
                data['crypto'].iloc[i] = data['crypto'].iloc[i - 1]
                data['cash'].iloc[i] = data['cash'].iloc[i - 1]
        else:
            # No trade signal, maintain the position
            data['crypto'].iloc[i] = data['crypto'].iloc[i - 1]
            data['cash'].iloc[i] = data['cash'].iloc[i - 1]

        # Calculate portfolio value
        data['strategy_portfolio_value'].iloc[i] = data['cash'].iloc[i] + data['crypto'].iloc[i] * data['close'].iloc[i]

        # Apply stop-loss if the portfolio value drops too much
        if position_open and data['strategy_portfolio_value'].iloc[i] / initial_investment < (1 - stop_loss_threshold):
            data['cash'].iloc[i] = data['crypto'].iloc[i] * data['close'].iloc[i] * (1 - taker_fee)
            data['crypto'].iloc[i] = 0
            position_open = False

        # Calculate strategy return as the relative change in portfolio value
        data['strategy_return'].iloc[i] = (data['strategy_portfolio_value'].iloc[i] / data['strategy_portfolio_value'].iloc[i - 1]) - 1

    # Calculate market portfolio value without strategy (buy and hold)
    data['market_portfolio_value'] = initial_investment * (data['close'] / data['close'].iloc[0])

    return data


def plot_backtest_results(data, pair, output_file):
    # Ensure that the 'timestamp' column is in datetime format and set as index
    data['timestamp'] = pd.to_datetime(data['timestamp'])
    data.set_index('timestamp', inplace=True)

    plt.figure(figsize=(14, 7))

    # Plot the strategy portfolio value and market portfolio value
    plt.plot(data.index, data['strategy_portfolio_value'], label='Strategy Portfolio Value (with fees)')
    plt.plot(data.index, data['market_portfolio_value'], label='Market Portfolio Value')

    # Format the x-axis to show dates
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    plt.gca().xaxis.set_major_locator(mdates.MonthLocator())  # Adjust the locator as needed

    plt.title(f'Backtest Results for {pair}')
    plt.xlabel('Date')
    plt.ylabel('Portfolio Value')
    plt.legend()
    plt.grid(True)
    plt.xticks(rotation=45)  # Rotate the x-axis labels for better readability

    # Save the plot as a PNG file
    plot_file = output_file.replace('.xlsx', '.png')
    plt.savefig(plot_file)
    plt.close()

async def run_backtest(pair, timeframe, token, values, stop_loss_threshold=0.05, initial_investment=10000, maker_fee=0.001, taker_fee=0.001, gain_threshold=0.001):
    start = datetime.now()

    # Generate dynamic file names
    model_path = get_model_path(pair, timeframe)
    output_file = f'{FILES_PATH}/backtest_results_{pair}_{timeframe}_{token}.xlsx'

    # If the model does not exist, train it from January 2023
    end_date = values.split('|')[1]
    initial_training_values = f'2023-01-01|{end_date}'
    train_values = initial_training_values if not os.path.exists(model_path) else values

    # Fetch historical data and add technical indicators
    await get_all_binance(pair, timeframe, token, save=True)
    data = await get_historical_data(pair, timeframe, train_values)
    data = add_indicators(data)

    # Ensure the return column is calculated and present in the DataFrame
    data['return'] = data['close'].pct_change().shift(-1)

    # Calculate initial amount of crypto based on initial investment
    initial_crypto_amount = initial_investment / data['close'].iloc[0]

    # Check if a trained model exists and load it, otherwise train a new one
    if os.path.exists(model_path):
        model = joblib.load(model_path)
        features = ['rsi', 'macd', 'macd_signal', 'macd_diff', 'bollinger_hband', 'bollinger_mavg', 'bollinger_lband', 'ema', 'ATR']
        # Update the model with new data if train_values != initial_training_values
        if train_values != initial_training_values:
            new_data = await get_historical_data(pair, timeframe, values)
            new_data = add_indicators(new_data)
            model = update_model(model, new_data, features)
    else:
        model, features = train_model(data, model_path)

    # Backtest the strategy with stop-loss, fees, and gain threshold
    backtest_result = await backtest(data, model, features, initial_investment, stop_loss_threshold, maker_fee, taker_fee, gain_threshold)

    # Get the last non-zero crypto value
    last_crypto_value = get_last_non_zero_crypto(backtest_result)

    # Display the final portfolio values
    final_strategy_value = backtest_result['strategy_portfolio_value'].iloc[-1]
    final_market_value = backtest_result['market_portfolio_value'].iloc[-1]

    # Calculate the final gain or loss in percentage
    final_percentage_gain_loss = ((final_strategy_value - initial_investment) / initial_investment) * 100

    # Calculate the number of months in the backtest period
    start_date = datetime.strptime(values.split('|')[0], '%Y-%m-%d')
    end_date = datetime.strptime(values.split('|')[1], '%Y-%m-%d')
    num_months = (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1

    # Calculate the average monthly gain or loss percentage
    avg_monthly_percentage_gain_loss = final_percentage_gain_loss / num_months

    # Save results to an Excel file
    backtest_result.to_excel(output_file, index=False)

    result_explanation = (
        f"Initial crypto amount (with initial investment): {initial_crypto_amount:.6f} {pair.split('USDT')[0]}\n"
        f"Final strategy portfolio value: ${final_strategy_value:.2f}\n"
        f"Final market portfolio value: ${final_market_value:.2f}\n"
        f"Final amount of {pair}: {last_crypto_value:.6f}\n"
        f"Final percentage gain/loss: {final_percentage_gain_loss:.2f}%\n"
        f"Average monthly percentage gain/loss: {avg_monthly_percentage_gain_loss:.2f}%\n"
        f"You have selected a gain threshold of {gain_threshold * 100:.2f}% and a stop-loss threshold of {stop_loss_threshold * 100:.2f}%.\n"
        f"Execution time: {datetime.now() - start}"
    )

    # Plot the results and save the plot as PNG
    plot_backtest_results(backtest_result, pair, output_file)

    return result_explanation


# Example of how to call run_backtest
# if __name__ == "__main__":
#     pair = 'SOLUSDT'
#     timeframe = '1h'
#     token = '556159355'
#     values = '2025-01-01|2025-01-19'
#     stop_loss_threshold = 0.5
#     initial_investment = 100
#     maker_fee = 0.001
#     taker_fee = 0.001
#     gain_threshold = 0.01

#     print(run_backtest(pair, timeframe, token, values, stop_loss_threshold, initial_investment, maker_fee, taker_fee, gain_threshold))
