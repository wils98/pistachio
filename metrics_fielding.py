from config import FIELDING_RUN_VALUES_VS_REPLACEMENT, RUNS_PER_WIN
from rating_lookup import interpolate_lookup

def calc_fielding_metrics(df):
    """
    Calculates defensive value per position and adds the following columns to df:
    C_def, CF_def, RF_def, LF_def, SS_def, 2B_def, 3B_def, 1B_def

    Each column represents estimated runs saved vs replacement at that position.
    """
    added_columns = []

    for position, ratings_dict in FIELDING_RUN_VALUES_VS_REPLACEMENT.items():
        total_def_column = f"{position}_def"
        def_values = []

        for _, row in df.iterrows():
            total = 0.0

            for rating_name, rating_map in ratings_dict.items():
                if rating_name in row:
                    player_rating = row[rating_name]
                    total += interpolate_lookup(player_rating, rating_map)

            def_values.append(total)

        df[total_def_column] = def_values
        df[total_def_column] = (df[total_def_column] / RUNS_PER_WIN).round(1) # convert from runs to wins i.e. fielding WAR
        added_columns.append(total_def_column)

    print(f"✅ Added fielding columns: {added_columns}")
    return df