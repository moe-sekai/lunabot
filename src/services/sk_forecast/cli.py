from fda_forecaster import FDAForecaster
from argparse import ArgumentParser
import pandas as pd
from datetime import datetime
import os


def forecast(
    history_csvs: list[str], 
    current_csv: str, 
    result_csv: str, 
    history_result_csv: str | None, 
    ranks: list[int],
    current_hours_to_use: float | None = None,
    current_rate_to_use: float | None = None,
):
    t = datetime.now()

    history_all = pd.concat([pd.read_csv(csv) for csv in history_csvs], ignore_index=True)
    current_all = pd.read_csv(current_csv)

    history_all["to_end_hour"] = history_all["to_end_hour"].abs()
    current_all["to_end_hour"] = current_all["to_end_hour"].abs()
    
    if current_hours_to_use is not None:
        current_all = current_all[current_all["from_start_hour"] <= current_hours_to_use]
    if current_rate_to_use is not None:
        current_length = current_all.iloc[0]['to_end_hour'] + current_all.iloc[0]['from_start_hour']
        current_all = current_all[current_all["from_start_hour"] <= current_rate_to_use * current_length]

    results = []
    all_final_scores = []

    for rank in ranks:
        current = current_all[current_all["rank"] == rank].sort_values(by="timestamp")
        history = history_all[history_all["rank"] == rank].sort_values(by=["event_id", "timestamp"])

        current_event_id = current["event_id"].iloc[0]

        forecaster = FDAForecaster()
        for event_id in history["event_id"].unique():
            if current_event_id == event_id:
                print("Skipping current event in history.")
                continue
            event_history = history[history["event_id"] == event_id]
            forecaster.add_history(event_history)

        res = forecaster.forecast(current)
        res['score'] = res['score'].round().astype(int)
        res['event_id'] = current_event_id
        res['rank'] = rank
        results.append(res)
        final_scores = res.iloc[-1]
        final_scores[['timestamp', 'from_start_hour', 'to_end_hour']] = current.iloc[-1][['timestamp', 'from_start_hour', 'to_end_hour']]
        all_final_scores.append(final_scores)

    result_df = pd.concat(results, ignore_index=True).sort_values(by=["rank", "timestamp"])
    os.makedirs(os.path.dirname(result_csv), exist_ok=True)
    result_df.to_csv(result_csv, index=False)

    if history_result_csv is not None:
        try:
            history_result_df = pd.read_csv(history_result_csv)
        except FileNotFoundError:
            print("History result CSV not found. Creating a new one.")
            history_result_df = pd.DataFrame()
        all_final_scores = pd.DataFrame(all_final_scores)
        history_result_df = pd.concat([history_result_df, all_final_scores], ignore_index=True)
        history_result_df.to_csv(history_result_csv, index=False)

    print(f"Forecasting done in {(datetime.now() - t).total_seconds():.2f} seconds.")



if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--history_csvs", type=str, required=True, help="""
        Paths to CSV files containing data of historical events, separated by comma
        Each CSV should have columns: event_id(int), rank(int), timestamp(int), from_start_hour(float), to_end_hour(float), score(int)
        """.strip())
    parser.add_argument("--current_csv", required=True, help="""
        Path to the input CSV file containing data of currrent event
        The CSV should have columns: event_id(int), rank(int), timestamp(int), from_start_hour(float), to_end_hour(float), score(int)
        """.strip())
    parser.add_argument("--result_csv", required=True, help="""
        Path to output CSV file to save forecast results of the future hours
        """.strip())
    parser.add_argument("--history_result_csv", required=False, help="""
        Path to output CSV file to update the historical results of forecasted final scores
        """.strip())
    parser.add_argument("--ranks", type=str, required=True, help="""
        Ranks to forecast, separated by comma
        """.strip())
    parser.add_argument("--current_hours_to_use", type=float, default=None, help="""
        Hours from start to use from current event data
        """.strip())
    parser.add_argument("--current_rate_to_use", type=float, default=None, help="""
        Rate of length to use from current event data
        """.strip())
    args = parser.parse_args()

    args.history_csvs = args.history_csvs.split(",")
    args.ranks = [int(r) for r in args.ranks.split(",")]

    forecast(
        history_csvs=args.history_csvs,
        current_csv=args.current_csv,
        result_csv=args.result_csv,
        history_result_csv=args.history_result_csv,
        ranks=args.ranks,
        current_hours_to_use=args.current_hours_to_use,
        current_rate_to_use=args.current_rate_to_use,
    )
    