import io
import os
import logging
import json
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_results_dir():
    # Try to read from environment variable
    res_dir = os.environ.get("RESULTS_DIR")
    if res_dir:
        return res_dir
        
    # Check multiple locations for results directory
    results_dir_candidates = [
        "/tmp_session_files/results",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "results"),
        os.path.join(os.getcwd(), "results"),
    ]

    for candidate in results_dir_candidates:
        if os.path.exists(candidate) and os.path.isdir(candidate):
            return candidate

    return results_dir_candidates[1]  # Fallback to default

def precompute():
    results_dir = get_results_dir()
    logging.info(f"Reading results from {results_dir}")
    
    if not os.path.exists(results_dir):
        logging.warning(f"Results directory not found at {results_dir}")
        return
        
    directories = [
        d
        for d in os.listdir(results_dir)
        if os.path.isdir(os.path.join(results_dir, d))
    ]
    
    data = []
    products = set()
    requesters = set()
    eval_ids = []
    
    total_dirs = len(directories)
    logging.info(f"Found {total_dirs} directories to process.")
    
    for i, d in enumerate(directories):
        eval_ids.append(d)
        
        if (i + 1) % 10 == 0 or i == total_dirs - 1:
            logging.info(f"Precompute progress: {(i + 1) / total_dirs * 100:.1f}% ({i + 1}/{total_dirs})")
        run_dir = os.path.join(results_dir, d)
        configs_file = os.path.join(run_dir, "configs.csv")
        summary_file = os.path.join(run_dir, "summary.csv")
        
        if os.path.exists(configs_file) and os.path.exists(summary_file):
            try:
                # Read configs
                configs_df = pd.read_csv(configs_file)
                
                # Extract requester and product
                requester_row = configs_df[configs_df['config'].str.contains('guitar_requester', na=False)]
                product_row = configs_df[configs_df['config'].isin(['experiment_config.product_name', 'experiment_config.poduct_name'])]
                
                requester = requester_row['value'].values[0] if not requester_row.empty else "unknown"
                product = product_row['value'].values[0] if not product_row.empty else "unknown"
                
                if product != "unknown" and str(product).strip() != "":
                    products.add(product)
                if requester != "unknown" and str(requester).strip() != "":
                    requesters.add(requester)
                
                # Read summary
                summary_df = pd.read_csv(summary_file)
                
                # Extract metrics
                latency_row = summary_df[summary_df['metric_name'] == 'end_to_end_latency']
                token_row = summary_df[summary_df['metric_name'] == 'token_consumption']
                trajectory_row = summary_df[summary_df['metric_name'] == 'trajectory_matcher']
                
                latency = float(latency_row['metric_score'].values[0]) if not latency_row.empty else 0.0
                tokens = float(token_row['metric_score'].values[0]) if not token_row.empty else 0.0
                trajectory = float(trajectory_row['metric_score'].values[0]) if not trajectory_row.empty else 0.0
                
                run_time = summary_df['run_time'].values[0] if not summary_df.empty else "unknown"
                if run_time != "unknown":
                    try:
                        run_time = pd.to_datetime(run_time).strftime('%Y-%m-%d')
                    except:
                        pass
                
                data.append({
                    'run_time': run_time,
                    'requester': requester,
                    'product': product,
                    'latency': latency,
                    'tokens': tokens,
                    'trajectory': trajectory,
                    'job_id': d
                })
            except Exception as e:
                print(f"Error reading data from {d}: {e}")
                
    if not data:
        logging.warning("No data found in any run directory.")
        return
        
    df = pd.DataFrame(data)
    
    # Save trends cache
    cache_file = os.path.join(results_dir, "trends_cache.csv")
    df.to_csv(cache_file, index=False)
    logging.info(f"Precomputed trends data saved to {cache_file}")
    
    # Save filters cache
    filters_file = os.path.join(results_dir, "filters_cache.json")
    filters_data = {
        "products": sorted(list(products)),
        "requesters": sorted(list(requesters)),
        "eval_ids": sorted(eval_ids)
    }
    with open(filters_file, "w") as f:
        json.dump(filters_data, f, indent=2)
    logging.info(f"Precomputed filter values saved to {filters_file}")

if __name__ == "__main__":
    precompute()
