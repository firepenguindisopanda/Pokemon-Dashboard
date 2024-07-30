import pandas as pd
from sklearn.preprocessing import OneHotEncoder
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
import numpy as np

def process_data(filepath):
    data = pd.read_csv(filepath)
    
    # Convert necessary columns to appropriate types
    data['height_m'] = pd.to_numeric(data['height_m'], errors='coerce')
    data['weight_kg'] = pd.to_numeric(data['weight_kg'], errors='coerce')
    data['generation'] = pd.to_numeric(data['generation'], errors='coerce')
    data['is_legendary'] = pd.to_numeric(data['is_legendary'], errors='coerce')
    
    # Drop rows with essential missing values
    data = data.dropna(subset=['name', 'type1', 'hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed'])
    
    return data

def build_recommendation_system(data):
    # For Pokémon, let's use type1 and type2 as the basis for similarity
    data['types'] = data['type1'].fillna('') + ' ' + data['type2'].fillna('')
    type_features = data['types']
    
    def get_recommendations(pokemon_name):
        # Find the Pokémon index
        idx = data.index[data['name'] == pokemon_name].tolist()
        if not idx:
            return pd.DataFrame()  # Pokémon not found
        idx = idx[0]
        
        # Calculate similarity based on types
        sim_scores = data['types'].apply(lambda x: similarity(data['types'][idx], x))
        sim_scores = sim_scores.sort_values(ascending=False)
        similar_indices = sim_scores.index[sim_scores > 0].tolist()
        
        # Exclude the queried Pokémon itself
        if idx in similar_indices:
            similar_indices.remove(idx)
        
        # Get top 10 similar Pokémon
        similar_indices = similar_indices[:10]
        return data.iloc[similar_indices][['name', 'type1', 'type2']]
    
    return get_recommendations

def similarity(type1, type2):
    # Simple similarity based on overlap
    set1 = set(type1.split())
    set2 = set(type2.split())
    return len(set1.intersection(set2)) / len(set1.union(set2))

def build_predictive_model(data):
    features = ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed', 'generation', 'is_legendary']
    data = data[features + ['base_total']].dropna()
    
    X = data[features]
    y = data['base_total']
    
    # One-hot encode 'generation' and 'is_legendary'
    categorical_features = ['generation', 'is_legendary']
    encoder = OneHotEncoder(handle_unknown='ignore')
    X_encoded = encoder.fit_transform(X[categorical_features]).toarray()
    X_encoded = np.hstack((X_encoded, X[['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed']].values))
    
    X_train, X_test, y_train, y_test = train_test_split(X_encoded, y, test_size=0.2, random_state=42)
    model = RandomForestRegressor(n_estimators=100, random_state=42)
    model.fit(X_train, y_train)
    
    return model, encoder

def predict_base_total(model, encoder, pokemon_features):
    categorical_features = ['generation', 'is_legendary']
    pokemon_data = pd.DataFrame([pokemon_features], columns=categorical_features + ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed'])
    
    pokemon_encoded = encoder.transform(pokemon_data[categorical_features]).toarray()
    pokemon_encoded = np.hstack((pokemon_encoded, pokemon_data[['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed']].values))
    
    predicted_base_total = model.predict(pokemon_encoded)
    return predicted_base_total[0]

def time_series_analysis(data):
    # Aggregate stats by generation
    generation_stats = data.groupby('generation')['base_total'].mean().reset_index()
    
    # Prepare the data for Highcharts
    highcharts_data = []
    for index, row in generation_stats.iterrows():
        highcharts_data.append([int(row['generation']), row['base_total']])
    
    return highcharts_data


data = process_data('pokemon.csv')

# Build the recommendation system and model
get_recommendations = build_recommendation_system(data)
predictive_model, encoder = build_predictive_model(data)

# Analyze time series data
time_series_data = time_series_analysis(data)
