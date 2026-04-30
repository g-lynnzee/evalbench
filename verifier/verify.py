#!/usr/bin/env python3
import os
import sys
import argparse
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRUTH_DIR = os.path.join(SCRIPT_DIR, "truth_data")

IGNORE_PATTERNS = ["job_id", "session_id"]

def clean_df(df):
    """Drops dynamic ID columns and resets indexing to stabilize comparisons."""
    cols_to_drop = [
        c for c in df.columns 
        if any(pattern in c.lower() for pattern in IGNORE_PATTERNS)
    ]
    cleaned = df.drop(columns=cols_to_drop, errors='ignore').copy()
    
    # Attempt logical sorting based on standardized filename identifiers
    # Default to stringifying and sorting by all columns if unique identifiers aren't obvious
    try:
        sort_keys = []
        common_keys = ["eval_id", "id", "config", "metric_name", "metric"]
        for k in common_keys:
            if k in cleaned.columns:
                sort_keys.append(k)
        if sort_keys:
            cleaned = cleaned.sort_values(by=sort_keys)
    except (KeyError, TypeError, ValueError):
        # Fallback gracefully if dataset cannot be natively ordered by defined keys
        pass
        
    return cleaned.reset_index(drop=True)

def compare_values(df_current, df_truth):
    """Performs value-wise analytics and returns comparison outcomes."""
    curr = clean_df(df_current)
    truth = clean_df(df_truth)
    
    # Structural quickcheck
    if curr.shape != truth.shape:
        return False, f"Shape mismatch (Current: {curr.shape} vs Truth: {truth.shape})"
    
    # Align column sets precisely
    common_cols = [c for c in truth.columns if c in curr.columns]
    if len(common_cols) != len(truth.columns):
         return False, "Column names mismatch prevents direct cell comparisons."
         
    # Mask discrepancies
    try:
        comparison_mask = (curr[common_cols] == truth[common_cols]) | (curr[common_cols].isna() & truth[common_cols].isna())
        match_pct = (comparison_mask.values.sum() / comparison_mask.size) * 100
        
        diffs = []
        if not comparison_mask.all().all():
            for col in common_cols:
                mismatched_count = (~comparison_mask[col]).sum()
                if mismatched_count > 0:
                    diffs.append(f"  ❌ {col}: {mismatched_count} rows differed")
                    
        if match_pct == 100.0:
             return True, "✅ 100% cell values match exactly (ignoring dynamic IDs)."
        else:
             details = "\n".join(diffs[:5])
             if len(diffs) > 5: details += f"\n  ...and {len(diffs)-5} other columns."
             return False, f"⚠️  {match_pct:.1f}% exact match. Diffs found:\n{details}"
    except Exception as e:
        return False, f"⚠️ Encountered error executing parallel comparison matrix: {e}"

def validate_and_compare(file_path, truth_path):
    filename = os.path.basename(file_path)
    print(f"\n{'='*80}")
    print(f"Analyzing: {filename}")
    print(f"{'='*80}")
    
    try:
        df = pd.read_csv(file_path, low_memory=False)
        
        # Check values if truth file exists
        val_msg = ""
        is_match = True
        if truth_path and os.path.exists(truth_path):
            try:
                truth_df = pd.read_csv(truth_path, low_memory=False)
                is_match, val_msg = compare_values(df, truth_df)
                print(val_msg)
            except Exception as compare_err:
                print(f"⚠️ Error during truth comparison lookup: {compare_err}")
        else:
            print("ℹ️ No baseline values found in truth_data for comparison.")

        print(f"\nDimensions: {len(df)} Rows × {len(df.columns)} Columns")
        if len(df) == 0:
            return not is_match

        print(f"\n{'COLUMN':<30} | {'FILL %':>8} | {'UNIQUE':>8} | {'SAMPLE (IGNORES IDS)'}")
        print("-" * 80)

        for column in df.columns:
            # Visual indicator for ignored columns
            is_ignored = any(p in column.lower() for p in IGNORE_PATTERNS)
            
            non_null_count = df[column].notna().sum()
            unique_count = df[column].nunique(dropna=True)
            fill_percentage = (non_null_count / len(df)) * 100
            
            sample_val = "HIDDEN (ID)" if is_ignored else "N/A"
            if not is_ignored:
                 first_valid = df[column].dropna()
                 if not first_valid.empty:
                     sample_val = str(first_valid.iloc[0])
                     sample_val = (sample_val[:35] + '...') if len(sample_val) > 35 else sample_val
                     sample_val = sample_val.replace('\n', '\\n')

            indicator = "[OK]"
            if is_ignored: indicator = "[ID]"
            elif fill_percentage == 0: indicator = "[!!]"

            print(f"{indicator} {column:<25} | {fill_percentage:>7.1f}% | {unique_count:>8} | {sample_val}")
            
        return not is_match
            
    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        return True

def main():
    parser = argparse.ArgumentParser(description="Value validation engine.")
    parser.add_argument("path", nargs="?", help="Session folder")
    parser.add_argument("--capture", action="store_true", help="Saves CSVs from target as the static truth repository.")
    args = parser.parse_args()

    session_dir = args.path
    if not session_dir:
        search_locations = ["/tmp_session_files/results", "results"]
        session_dir = get_latest_directory(search_locations)

    if not session_dir or not os.path.exists(session_dir):
        print("ERROR: Invalid path.")
        sys.exit(1)

    csv_files = [f for f in os.listdir(session_dir) if f.endswith(".csv")]
    
    if args.capture:
        print(f"--- CAPTURING DATAFRAMES AS THE BASELINE IN {TRUTH_DIR} ---")
        import shutil
        os.makedirs(TRUTH_DIR, exist_ok=True)
        for f in csv_files:
            shutil.copy2(os.path.join(session_dir, f), os.path.join(TRUTH_DIR, f))
        print("Archived baseline successfully.")
        sys.exit(0)

    print(f"\nCommencing deep value assertion pass on: {os.path.abspath(session_dir)}")
    any_failures = False
    
    for f in sorted(csv_files):
        t_path = os.path.join(TRUTH_DIR, f)
        res = validate_and_compare(os.path.join(session_dir, f), t_path)
        if res: any_failures = True
        
    print("\n--- Finished ---")
    if any_failures:
        print("🏁 FINAL STATUS: ⚠️ VALUE DEVIATIONS DETECTED IN DATASETS")
    else:
        print("🏁 FINAL STATUS: ✅ EXACT DATA VALUES MATCH BASELINE")

def get_latest_directory(base_dirs):
    all_dirs = []
    for base_dir in base_dirs:
        if os.path.exists(base_dir):
            all_dirs.extend([os.path.join(base_dir, d) for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))])
    return max(all_dirs, key=os.path.getmtime) if all_dirs else None

if __name__ == "__main__":
    main()
