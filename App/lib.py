import warnings
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression, LinearRegression
from sklearn.metrics import accuracy_score, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler
import ast

warnings.filterwarnings('ignore')

class PokemonAnalytics:
    def __init__(self):
        self.data = None
        self.cleaned_data = None
        self.models = {}
        self.scalers = {}
        self.encoders = {}
    
    def load_data(self, file_path=None, data=None):
        """
        Load Pokemon dataset from file or DataFrame
        Args:
            file_path (str): Path to CSV file
            data (DataFrame): Pre-loaded DataFrame
        Returns:
            DataFrame: Raw loaded data
        """
        if data is not None:
            self.data = data.copy()
        elif file_path:
            self.data = pd.read_csv(file_path)
        else:
            raise ValueError("Either file_path or data must be provided")
        return self.data
    
    def clean_data(self):
        """
        Clean and preprocess the Pokemon dataset
        Returns:
            DataFrame: Cleaned data
        """
        if self.data is None:
            raise ValueError("Data must be loaded first using load_data()")
        
        df = self.data.copy()
        
        # Handle missing values
        df['height_m'] = pd.to_numeric(df['height_m'], errors='coerce')
        df['weight_kg'] = pd.to_numeric(df['weight_kg'], errors='coerce')
        df['percentage_male'] = pd.to_numeric(df['percentage_male'], errors='coerce')
        
        # Fill missing values
        df['height_m'] = df['height_m'].fillna(df['height_m'].median())
        df['weight_kg'] = df['weight_kg'].fillna(df['weight_kg'].median())
        df['percentage_male'] = df['percentage_male'].fillna(50.0)  # Assume 50% if unknown
        df['type2'] = df['type2'].fillna('None')
        
        # Create derived features
        df['has_dual_type'] = (df['type2'] != 'None').astype(int)
        df['bmi'] = df['weight_kg'] / (df['height_m'] ** 2)
        df['bmi'] = df['bmi'].fillna(df['bmi'].median())
        
        # Create power level categories
        df['power_level'] = pd.cut(df['base_total'], 
                                 bins=[0, 300, 450, 600, 800], 
                                 labels=['Weak', 'Average', 'Strong', 'Legendary'])
        
        # Create stat efficiency ratios
        df['attack_defense_ratio'] = df['attack'] / (df['defense'] + 1)
        df['speed_hp_ratio'] = df['speed'] / (df['hp'] + 1)
        df['special_ratio'] = df['sp_attack'] / (df['sp_defense'] + 1)
        
        # Parse abilities (assuming they're stored as string representations of lists)
        def parse_abilities(abilities_str):
            try:
                return len(ast.literal_eval(abilities_str))
            except:
                return 1
        
        df['num_abilities'] = df['abilities'].apply(parse_abilities)
        
        # Create generation groups
        df['generation_group'] = pd.cut(df['generation'], 
                                      bins=[0, 2, 4, 6, 10], 
                                      labels=['Classic', 'Modern', 'Recent', 'Latest'])
        
        # Create catch difficulty categories
        df['catch_difficulty'] = pd.cut(df['capture_rate'], 
                                      bins=[0, 50, 100, 200, 255], 
                                      labels=['Very Hard', 'Hard', 'Medium', 'Easy'])
        
        self.cleaned_data = df
        return df
    
    def get_descriptive_stats(self):
        """
        Calculate descriptive statistics for Pokemon data
        Returns:
            dict: Dictionary containing various descriptive statistics
        """
        if self.cleaned_data is None:
            raise ValueError("Data must be cleaned first using clean_data()")
        
        df = self.cleaned_data
        
        stats = {
            'total_pokemon': int(len(df)),
            'legendary_count': int(df['is_legendary'].sum()),
            'legendary_rate': float(df['is_legendary'].mean()),
            
            'stat_averages': {
                'hp': float(df['hp'].mean()),
                'attack': float(df['attack'].mean()),
                'defense': float(df['defense'].mean()),
                'sp_attack': float(df['sp_attack'].mean()),
                'sp_defense': float(df['sp_defense'].mean()),
                'speed': float(df['speed'].mean()),
                'base_total': float(df['base_total'].mean())
            },
            
            'type_distribution': df['type1'].value_counts().to_dict(),
            'generation_distribution': df['generation'].value_counts().to_dict(),
            'dual_type_rate': float(df['has_dual_type'].mean()),
            
            'strongest_pokemon': df.nlargest(10, 'base_total')[
                ['name', 'base_total', 'type1', 'type2', 'is_legendary']
            ].to_dict('records'),
            
            'fastest_pokemon': df.nlargest(10, 'speed')[
                ['name', 'speed', 'type1', 'attack']
            ].to_dict('records'),
            
            'rarest_pokemon': df.nsmallest(10, 'capture_rate')[
                ['name', 'capture_rate', 'is_legendary', 'base_total']
            ].to_dict('records'),
            
            'physical_stats': {
                'avg_height': float(df['height_m'].mean()),
                'avg_weight': float(df['weight_kg'].mean()),
                'heaviest': df.nlargest(5, 'weight_kg')[['name', 'weight_kg']].to_dict('records'),
                'tallest': df.nlargest(5, 'height_m')[['name', 'height_m']].to_dict('records')
            }
        }
        
        return stats
    
    def diagnostic_analysis(self):
        """
        Perform diagnostic comparisons and correlation analysis
        Returns:
            dict: Dictionary containing diagnostic analysis results
        """
        if self.cleaned_data is None:
            raise ValueError("Data must be cleaned first using clean_data()")
        
        df = self.cleaned_data
        
        # Helper function to convert groupby results
        def convert_groupby_to_dict(grouped_data):
            result = {'mean': {}, 'count': {}}
            for key, value in grouped_data['mean'].items():
                result['mean'][str(key)] = float(value)
            for key, value in grouped_data['count'].items():
                result['count'][str(key)] = int(value)
            return result
        
        # Stats by type
        stats_by_type1 = convert_groupby_to_dict(
            df.groupby('type1')['base_total'].agg(['mean', 'count']).to_dict()
        )
        
        stats_by_generation = convert_groupby_to_dict(
            df.groupby('generation')['base_total'].agg(['mean', 'count']).to_dict()
        )
        
        legendary_by_type = convert_groupby_to_dict(
            df.groupby('type1')['is_legendary'].agg(['mean', 'count']).to_dict()
        )
        
        # Correlations for numerical features
        numerical_cols = [
            'hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed',
            'base_total', 'height_m', 'weight_kg', 'capture_rate', 'generation',
            'is_legendary', 'has_dual_type', 'bmi'
        ]
        
        correlations = {}
        corr_data = df[numerical_cols].corr()['base_total'].drop('base_total')
        for key, value in corr_data.items():
            correlations[str(key)] = float(value) if not pd.isna(value) else 0.0
        
        # Type effectiveness analysis (simplified)
        type_combinations = df.dropna(subset=['type2'])
        popular_combos = type_combinations.groupby(['type1', 'type2']).size().nlargest(10)
        popular_combinations = {}
        for (type1, type2), count in popular_combos.items():
            popular_combinations[f"{type1}_{type2}"] = int(count)
        
        diagnostics = {
            'stats_by_type': stats_by_type1,
            'stats_by_generation': stats_by_generation,
            'legendary_by_type': legendary_by_type,
            'correlations': correlations,
            'popular_type_combinations': popular_combinations,
            'stat_correlations': {
                'attack_defense': float(df['attack'].corr(df['defense'])),
                'sp_attack_sp_defense': float(df['sp_attack'].corr(df['sp_defense'])),
                'speed_attack': float(df['speed'].corr(df['attack'])),
                'height_weight': float(df['height_m'].corr(df['weight_kg']))
            }
        }
        
        return diagnostics
    
    def train_predictive_models(self, model_params=None):
        """
        Train predictive models for Pokemon stats prediction
        """
        if self.cleaned_data is None:
            raise ValueError("Data must be cleaned first using clean_data()")
        
        df = self.cleaned_data.copy()
        
        # Encode categorical variables
        le_type1 = LabelEncoder()
        le_type2 = LabelEncoder()
        le_generation_group = LabelEncoder()
        
        df['type1_encoded'] = le_type1.fit_transform(df['type1'])
        df['type2_encoded'] = le_type2.fit_transform(df['type2'])
        df['generation_group_encoded'] = le_generation_group.fit_transform(df['generation_group'].astype(str))
        
        self.encoders = {
            'type1': le_type1,
            'type2': le_type2,
            'generation_group': le_generation_group
        }
        
        # Features for base_total prediction
        feature_cols = [
            'hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed',
            'type1_encoded', 'type2_encoded', 'generation', 'has_dual_type',
            'height_m', 'weight_kg', 'capture_rate', 'num_abilities'
        ]
        
        # Model 1: Predict if Pokemon is legendary
        X_legendary = df[feature_cols + ['base_total']]
        y_legendary = df['is_legendary']
        
        X_train_leg, X_test_leg, y_train_leg, y_test_leg = train_test_split(
            X_legendary, y_legendary, test_size=0.2, random_state=42
        )
        
        # Model 2: Predict base total stats
        X_stats = df[feature_cols[:-1]]  # Exclude base_total from features
        y_stats = df['base_total']
        
        X_train_stats, X_test_stats, y_train_stats, y_test_stats = train_test_split(
            X_stats, y_stats, test_size=0.2, random_state=42
        )
        
        # Scale features
        scaler_legendary = StandardScaler()
        scaler_stats = StandardScaler()
        
        X_train_leg_scaled = scaler_legendary.fit_transform(X_train_leg)
        X_test_leg_scaled = scaler_legendary.transform(X_test_leg)
        
        X_train_stats_scaled = scaler_stats.fit_transform(X_train_stats)
        X_test_stats_scaled = scaler_stats.transform(X_test_stats)
        
        self.scalers = {
            'legendary': scaler_legendary,
            'stats': scaler_stats
        }
        
        # Default parameters
        defaults = {
            'random_forest_classifier': {
                'n_estimators': 100,
                'max_depth': 10,
                'random_state': 42
            },
            'random_forest_regressor': {
                'n_estimators': 100,
                'max_depth': 10,
                'random_state': 42
            },
            'logistic_regression': {
                'C': 1.0,
                'random_state': 42
            }
        }
        
        if model_params:
            for key, params in model_params.items():
                if key in defaults:
                    defaults[key].update(params)
        
        results = {}
        
        # Train legendary prediction model
        rf_classifier = RandomForestClassifier(**defaults['random_forest_classifier'])
        rf_classifier.fit(X_train_leg, y_train_leg)
        y_pred_leg = rf_classifier.predict(X_test_leg)
        
        legendary_accuracy = accuracy_score(y_test_leg, y_pred_leg)
        
        # Feature importance for legendary prediction
        feature_names_leg = feature_cols + ['base_total']
        leg_importance = {name: float(imp) for name, imp in 
                         zip(feature_names_leg, rf_classifier.feature_importances_)}
        
        self.models['legendary_classifier'] = rf_classifier
        results['legendary_prediction'] = {
            'accuracy': float(legendary_accuracy),
            'feature_importance': leg_importance
        }
        
        # Train stats prediction model
        rf_regressor = RandomForestRegressor(**defaults['random_forest_regressor'])
        rf_regressor.fit(X_train_stats, y_train_stats)
        y_pred_stats = rf_regressor.predict(X_test_stats)
        
        stats_r2 = r2_score(y_test_stats, y_pred_stats)
        stats_mse = mean_squared_error(y_test_stats, y_pred_stats)
        
        # Feature importance for stats prediction
        feature_names_stats = feature_cols[:-1]
        stats_importance = {name: float(imp) for name, imp in 
                          zip(feature_names_stats, rf_regressor.feature_importances_)}
        
        self.models['stats_regressor'] = rf_regressor
        results['stats_prediction'] = {
            'r2_score': float(stats_r2),
            'mse': float(stats_mse),
            'feature_importance': stats_importance
        }
        
        return results
    
    def predict_pokemon_stats(self, pokemon_data):
        """
        Predict Pokemon stats and legendary status
        Args:
            pokemon_data (dict): Dictionary with Pokemon features
        Returns:
            dict: Prediction results
        """
        if not self.models:
            raise ValueError("Models must be trained first using train_predictive_models()")
        
        # Create DataFrame from input
        df = pd.DataFrame([pokemon_data])
        
        # Apply encodings
        df['type1_encoded'] = self.encoders['type1'].transform(df['type1'])
        df['type2_encoded'] = self.encoders['type2'].transform(df['type2'])
        
        # Create derived features
        df['has_dual_type'] = (df['type2'] != 'None').astype(int)
        df['bmi'] = df['weight_kg'] / (df['height_m'] ** 2)
        
        predictions = {}
        
        # Predict legendary status
        if 'legendary_classifier' in self.models:
            feature_cols_leg = [
                'hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed',
                'type1_encoded', 'type2_encoded', 'generation', 'has_dual_type',
                'height_m', 'weight_kg', 'capture_rate', 'num_abilities', 'base_total'
            ]
            X_leg = df[feature_cols_leg]
            
            legendary_prob = self.models['legendary_classifier'].predict_proba(X_leg)[0]
            legendary_pred = self.models['legendary_classifier'].predict(X_leg)[0]
            
            predictions['legendary_prediction'] = {
                'is_legendary': int(legendary_pred),
                'legendary_probability': float(legendary_prob[1]),
                'normal_probability': float(legendary_prob[0])
            }
        
        # Predict base total
        if 'stats_regressor' in self.models:
            feature_cols_stats = [
                'hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed',
                'type1_encoded', 'type2_encoded', 'generation', 'has_dual_type',
                'height_m', 'weight_kg', 'capture_rate', 'num_abilities'
            ]
            X_stats = df[feature_cols_stats]
            
            base_total_pred = self.models['stats_regressor'].predict(X_stats)[0]
            
            predictions['stats_prediction'] = {
                'predicted_base_total': float(base_total_pred)
            }
        
        return predictions
    
    def perform_clustering(self, n_clusters=5):
        """
        Perform clustering analysis on Pokemon
        Args:
            n_clusters (int): Number of clusters
        Returns:
            dict: Clustering results and analysis
        """
        if self.cleaned_data is None:
            raise ValueError("Data must be cleaned first using clean_data()")
        
        df = self.cleaned_data.copy()
        
        # Prepare features for clustering
        le_type1 = LabelEncoder()
        df['type1_encoded'] = le_type1.fit_transform(df['type1'])
        
        clustering_features = [
            'hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed',
            'type1_encoded', 'generation', 'height_m', 'weight_kg'
        ]
        
        X = df[clustering_features].fillna(df[clustering_features].median())
        
        # Scale features
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        # Perform clustering
        kmeans = KMeans(n_clusters=n_clusters, random_state=42)
        clusters = kmeans.fit_predict(X_scaled)
        df['Cluster'] = clusters
        
        # Analyze clusters
        cluster_analysis = {}
        for i in range(n_clusters):
            cluster_data = df[df['Cluster'] == i]
            
            type_dist = cluster_data['type1'].value_counts().head(3).to_dict()
            gen_dist = cluster_data['generation'].value_counts().to_dict()
            
            cluster_analysis[f'cluster_{i}'] = {
                'size': int(len(cluster_data)),
                'legendary_rate': float(cluster_data['is_legendary'].mean()),
                'avg_base_total': float(cluster_data['base_total'].mean()),
                'avg_capture_rate': float(cluster_data['capture_rate'].mean()),
                'dominant_types': {str(k): int(v) for k, v in type_dist.items()},
                'generation_distribution': {str(k): int(v) for k, v in gen_dist.items()},
                'stats_profile': {
                    'hp': float(cluster_data['hp'].mean()),
                    'attack': float(cluster_data['attack'].mean()),
                    'defense': float(cluster_data['defense'].mean()),
                    'speed': float(cluster_data['speed'].mean())
                }
            }
        
        return {
            'cluster_analysis': cluster_analysis,
            'total_clusters': n_clusters,
            'cluster_assignments': clusters.tolist()
        }
    
    def optimize_pokemon_build(self, target_stat='base_total'):
        """
        Optimize Pokemon characteristics for maximum performance
        Args:
            target_stat (str): The stat to optimize for
        Returns:
            dict: Optimization results
        """
        if not self.models or 'stats_regressor' not in self.models:
            raise ValueError("Stats regression model must be trained first")
        
        model = self.models['stats_regressor']
        
        def pokemon_performance(params):
            # params: [hp, attack, defense, sp_attack, sp_defense, speed, type1_encoded, type2_encoded, generation, has_dual_type, height_m, weight_kg, capture_rate, num_abilities]
            features = np.array(params).reshape(1, -1)
            prediction = model.predict(features)[0]
            return -prediction  # Negative because we want to maximize
        
        # Define bounds for optimization
        bounds = [
            (1, 255),    # hp
            (1, 255),    # attack  
            (1, 255),    # defense
            (1, 255),    # sp_attack
            (1, 255),    # sp_defense
            (1, 255),    # speed
            (0, 17),     # type1_encoded (approximate number of types)
            (0, 17),     # type2_encoded
            (1, 8),      # generation
            (0, 1),      # has_dual_type
            (0.1, 20),   # height_m
            (0.1, 1000), # weight_kg
            (3, 255),    # capture_rate
            (1, 3)       # num_abilities
        ]
        
        # Initial guess (balanced stats)
        x0 = [85, 85, 85, 85, 85, 85, 0, 1, 5, 1, 1.5, 50, 45, 2]
        
        # Optimize
        result = minimize(pokemon_performance, x0, bounds=bounds, method='L-BFGS-B')
        optimal_features = result.x
        max_performance = -result.fun
        
        # Create readable interpretation
        optimal_pokemon = {
            'hp': int(round(optimal_features[0])),
            'attack': int(round(optimal_features[1])),
            'defense': int(round(optimal_features[2])),
            'sp_attack': int(round(optimal_features[3])),
            'sp_defense': int(round(optimal_features[4])),
            'speed': int(round(optimal_features[5])),
            'generation': int(round(optimal_features[8])),
            'height_m': round(optimal_features[10], 2),
            'weight_kg': round(optimal_features[11], 2),
            'capture_rate': int(round(optimal_features[12])),
            'num_abilities': int(round(optimal_features[13])),
            'predicted_base_total': round(max_performance, 2)
        }
        
        return optimal_pokemon

    def recommend_team(self, preferences=None):
        """
        Recommend a team of 6 Pokemon based on user preferences
        Args:
            preferences (dict): User preferences for team composition (all optional)
                - playstyle: 'offensive', 'defensive', 'balanced', 'speed'
                - preferred_types: list of preferred types (optional)
                - generation_preference: preferred generation range (optional)
                - include_legendary: boolean, whether to include legendaries
                - difficulty_level: 'beginner', 'intermediate', 'advanced'
                - team_role_balance: boolean, whether to balance team roles
        Returns:
            dict: Recommended team with analysis
        """
        if self.cleaned_data is None:
            raise ValueError("Data must be cleaned first using clean_data()")
        
        # Handle case where no preferences are provided
        if preferences is None:
            preferences = {}
        
        df = self.cleaned_data.copy()
        
        # Filter based on preferences
        filtered_df = df.copy()
        
        # Filter by legendary preference (default to include some legendaries but not dominated)
        include_legendary = preferences.get('include_legendary')
        if include_legendary is False:
            # Explicitly exclude legendaries
            filtered_df = filtered_df[filtered_df['is_legendary'] == False]
        elif include_legendary is True:
            # Include legendaries (no filtering)
            pass
        else:
            # Default behavior: allow legendaries but prefer non-legendaries (80/20 split)
            if len(filtered_df[filtered_df['is_legendary'] == False]) >= 6:
                # If we have enough non-legendaries, favor them but allow some legendaries
                pass  # Use full dataset with bias in selection later
        
        # Filter by generation preference
        if 'generation_preference' in preferences:
            gen_range = preferences['generation_preference']
            if isinstance(gen_range, list) and len(gen_range) == 2:
                filtered_df = filtered_df[
                    (filtered_df['generation'] >= gen_range[0]) & 
                    (filtered_df['generation'] <= gen_range[1])
                ]
        
        # Filter by preferred types
        if 'preferred_types' in preferences and preferences['preferred_types']:
            type_filter = filtered_df['type1'].isin(preferences['preferred_types']) | \
                         filtered_df['type2'].isin(preferences['preferred_types'])
            filtered_df = filtered_df[type_filter]
        
        # Define team roles and selection criteria based on playstyle
        playstyle = preferences.get('playstyle', 'balanced')
        
        if playstyle == 'offensive':
            # High attack/sp_attack focus
            filtered_df['role_score'] = (
                filtered_df['attack'] * 0.4 + 
                filtered_df['sp_attack'] * 0.4 + 
                filtered_df['speed'] * 0.2
            )
        elif playstyle == 'defensive':
            # High defense/sp_defense/hp focus
            filtered_df['role_score'] = (
                filtered_df['defense'] * 0.3 + 
                filtered_df['sp_defense'] * 0.3 + 
                filtered_df['hp'] * 0.4
            )
        elif playstyle == 'speed':
            # Speed-focused team
            filtered_df['role_score'] = (
                filtered_df['speed'] * 0.6 + 
                filtered_df['attack'] * 0.2 + 
                filtered_df['sp_attack'] * 0.2
            )
        else:  # balanced (default)
            # Balanced approach using base_total
            filtered_df['role_score'] = filtered_df['base_total']
        
        # Define team roles for balanced teams
        team_roles = []
        if preferences.get('team_role_balance', True):
            team_roles = [
                {'name': 'Physical Attacker', 'weight': {'attack': 0.6, 'speed': 0.4}},
                {'name': 'Special Attacker', 'weight': {'sp_attack': 0.6, 'speed': 0.4}},
                {'name': 'Tank', 'weight': {'hp': 0.4, 'defense': 0.3, 'sp_defense': 0.3}},
                {'name': 'Fast Support', 'weight': {'speed': 0.7, 'hp': 0.3}},
                {'name': 'Balanced', 'weight': {'base_total': 1.0}},
                {'name': 'Wildcard', 'weight': {'base_total': 1.0}}
            ]
        else:
            # Just pick top 6 based on role_score
            team_roles = [{'name': f'Pokemon_{i+1}', 'weight': {'role_score': 1.0}} for i in range(6)]
        
        recommended_team = []
        used_pokemon = set()
        
        for role in team_roles:
            # Calculate role-specific score
            if 'base_total' in role['weight']:
                role_df = filtered_df.copy()
                role_df['current_role_score'] = role_df['base_total']
            elif 'role_score' in role['weight']:
                role_df = filtered_df.copy()
                role_df['current_role_score'] = role_df['role_score']
            else:
                role_df = filtered_df.copy()
                role_df['current_role_score'] = 0
                for stat, weight in role['weight'].items():
                    if stat in role_df.columns:
                        role_df['current_role_score'] += role_df[stat] * weight
            
            # Exclude already used Pokemon
            available_df = role_df[~role_df['name'].isin(used_pokemon)]
            
            if len(available_df) == 0:
                continue
            
            # Apply legendary bias if no explicit preference
            include_legendary = preferences.get('include_legendary')
            if include_legendary is None:
                # Apply smart legendary selection: max 1-2 legendaries per team
                legendary_count = sum(1 for p in recommended_team if p.get('is_legendary', False))
                if legendary_count >= 2:
                    # Limit legendaries to 2 max
                    available_df = available_df[available_df['is_legendary'] == False]
                elif legendary_count == 0 and len(recommended_team) >= 3:
                    # Allow 1 legendary after we have some team members
                    legendary_candidates = available_df[available_df['is_legendary'] == True]
                    if len(legendary_candidates) > 0 and np.random.random() < 0.3:
                        # 30% chance to pick a legendary
                        available_df = legendary_candidates
            
            # Adjust selection based on difficulty level (default: intermediate)
            difficulty = preferences.get('difficulty_level', 'intermediate')
            if difficulty == 'beginner':
                # Prefer Pokemon with higher capture rates (easier to catch) and lower overall stats
                available_df = available_df[available_df['base_total'] <= 500]  # Cap power level
                available_df['final_score'] = (
                    available_df['current_role_score'] * 0.6 + 
                    available_df['capture_rate'] * 0.4
                )
            elif difficulty == 'advanced':
                # Focus purely on performance
                available_df['final_score'] = available_df['current_role_score']
            else:  # intermediate
                # Balance performance and catchability
                available_df['final_score'] = (
                    available_df['current_role_score'] * 0.8 + 
                    available_df['capture_rate'] * 0.2
                )
            
            # Select best Pokemon for this role
            best_pokemon = available_df.nlargest(1, 'final_score').iloc[0]
            
            pokemon_info = {
                'name': best_pokemon['name'],
                'type1': best_pokemon['type1'],
                'type2': best_pokemon['type2'] if best_pokemon['type2'] != 'None' else None,
                'role': role['name'],
                'stats': {
                    'hp': int(best_pokemon['hp']),
                    'attack': int(best_pokemon['attack']),
                    'defense': int(best_pokemon['defense']),
                    'sp_attack': int(best_pokemon['sp_attack']),
                    'sp_defense': int(best_pokemon['sp_defense']),
                    'speed': int(best_pokemon['speed']),
                    'base_total': int(best_pokemon['base_total'])
                },
                'generation': int(best_pokemon['generation']),
                'is_legendary': bool(best_pokemon['is_legendary']),
                'capture_rate': int(best_pokemon['capture_rate']),
                'height_m': float(best_pokemon['height_m']),
                'weight_kg': float(best_pokemon['weight_kg'])
            }
            
            recommended_team.append(pokemon_info)
            used_pokemon.add(best_pokemon['name'])
        
        # Team analysis
        team_df = pd.DataFrame([p['stats'] for p in recommended_team])
        team_types = [p['type1'] for p in recommended_team] + \
                    [p['type2'] for p in recommended_team if p['type2']]
        
        team_analysis = {
            'total_base_stats': int(team_df['base_total'].sum()),
            'average_base_total': float(team_df['base_total'].mean()),
            'team_stat_totals': {
                'hp': int(team_df['hp'].sum()),
                'attack': int(team_df['attack'].sum()),
                'defense': int(team_df['defense'].sum()),
                'sp_attack': int(team_df['sp_attack'].sum()),
                'sp_defense': int(team_df['sp_defense'].sum()),
                'speed': int(team_df['speed'].sum())
            },
            'team_stat_averages': {
                'hp': float(team_df['hp'].mean()),
                'attack': float(team_df['attack'].mean()),
                'defense': float(team_df['defense'].mean()),
                'sp_attack': float(team_df['sp_attack'].mean()),
                'sp_defense': float(team_df['sp_defense'].mean()),
                'speed': float(team_df['speed'].mean())
            },
            'type_coverage': list(set(team_types)),
            'legendary_count': sum(1 for p in recommended_team if p['is_legendary']),
            'generation_spread': list(set(p['generation'] for p in recommended_team)),
            'average_capture_rate': float(np.mean([p['capture_rate'] for p in recommended_team])),
            'team_strengths': self._analyze_team_strengths(team_df),
            'team_weaknesses': self._analyze_team_weaknesses(team_df),
            'synergy_score': self._calculate_team_synergy(team_df, playstyle)
        }
        
        return {
            'recommended_team': recommended_team,
            'team_analysis': team_analysis,
            'preferences_used': preferences,
            'total_pokemon_considered': len(filtered_df)
        }
    
    def _analyze_team_strengths(self, team_df):
        """Analyze team's statistical strengths"""
        strengths = []
        stat_means = team_df.mean()
        
        # Define thresholds for strong stats
        thresholds = {
            'hp': 80,
            'attack': 100,
            'defense': 90,
            'sp_attack': 100,
            'sp_defense': 90,
            'speed': 95
        }
        
        for stat, threshold in thresholds.items():
            if stat_means[stat] >= threshold:
                strengths.append(f"High {stat.replace('_', ' ').title()}")
        
        if stat_means['base_total'] >= 500:
            strengths.append("Strong Overall Stats")
        
        return strengths
    
    def _analyze_team_weaknesses(self, team_df):
        """Analyze team's potential weaknesses"""
        weaknesses = []
        stat_means = team_df.mean()
        
        # Define thresholds for weak stats
        thresholds = {
            'hp': 60,
            'attack': 70,
            'defense': 70,
            'sp_attack': 70,
            'sp_defense': 70,
            'speed': 60
        }
        
        for stat, threshold in thresholds.items():
            if stat_means[stat] <= threshold:
                weaknesses.append(f"Low {stat.replace('_', ' ').title()}")
        
        return weaknesses
    
    def _calculate_team_synergy(self, team_df, playstyle):
        """Calculate a synergy score based on how well the team fits the playstyle"""
        stat_means = team_df.mean()
        
        if playstyle == 'offensive':
            # Reward high attack stats and speed
            synergy = (stat_means['attack'] * 0.3 + 
                      stat_means['sp_attack'] * 0.3 + 
                      stat_means['speed'] * 0.4) / 100
        elif playstyle == 'defensive':
            # Reward high defensive stats and HP
            synergy = (stat_means['defense'] * 0.3 + 
                      stat_means['sp_defense'] * 0.3 + 
                      stat_means['hp'] * 0.4) / 100
        elif playstyle == 'speed':
            # Heavily reward speed
            synergy = (stat_means['speed'] * 0.7 + 
                      stat_means['attack'] * 0.15 + 
                      stat_means['sp_attack'] * 0.15) / 100
        else:  # balanced
            # Reward overall balance
            synergy = stat_means['base_total'] / 600
        
        return min(float(synergy), 1.0)  # Cap at 1.0
    
    def get_team_recommendations_by_theme(self, theme='competitive'):
        """
        Get pre-defined team recommendations based on popular themes
        Args:
            theme (str): Theme for team recommendation
                - 'competitive': Meta competitive team
                - 'starter_friendly': Good for beginners
                - 'legendary': Legendary-focused team
                - 'type_specialist': Single-type focused team
                - 'generation_classic': Classic generation 1 team
        Returns:
            dict: Themed team recommendation
        """
        if self.cleaned_data is None:
            raise ValueError("Data must be cleaned first using clean_data()")
        
        theme_preferences = {
            'competitive': {
                'playstyle': 'balanced',
                'include_legendary': False,
                'difficulty_level': 'advanced',
                'team_role_balance': True
            },
            'starter_friendly': {
                'playstyle': 'balanced',
                'include_legendary': False,
                'difficulty_level': 'beginner',
                'team_role_balance': False,
                'generation_preference': [1, 3]
            },
            'legendary': {
                'playstyle': 'offensive',
                'include_legendary': True,
                'difficulty_level': 'advanced',
                'team_role_balance': True
            },
            'generation_classic': {
                'playstyle': 'balanced',
                'include_legendary': False,
                'difficulty_level': 'intermediate',
                'team_role_balance': True,
                'generation_preference': [1, 1]
            }
        }
        
        if theme not in theme_preferences:
            raise ValueError(f"Unknown theme: {theme}. Available themes: {list(theme_preferences.keys())}")
        
        preferences = theme_preferences[theme]
        
        # For type specialist, pick a random strong type
        if theme == 'type_specialist':
            df = self.cleaned_data
            type_stats = df.groupby('type1')['base_total'].agg(['mean', 'count'])
            strong_types = type_stats[type_stats['count'] >= 10].nlargest(5, 'mean').index.tolist()
            selected_type = np.random.choice(strong_types)
            preferences.update({
                'playstyle': 'balanced',
                'preferred_types': [selected_type],
                'include_legendary': False,
                'difficulty_level': 'intermediate',
                'team_role_balance': False
            })
        
        result = self.recommend_team(preferences)
        result['theme'] = theme
        result['theme_description'] = self._get_theme_description(theme)
        
        return result
    
    def _get_theme_description(self, theme):
        """Get description for team themes"""
        descriptions = {
            'competitive': "A well-balanced team focused on competitive viability with strong stats and type coverage.",
            'starter_friendly': "A beginner-friendly team with easy-to-catch Pokemon from early generations.",
            'legendary': "A powerful team featuring legendary Pokemon for maximum impact.",
            'type_specialist': "A team focused on a single type for specialized strategies.",
            'generation_classic': "A nostalgic team featuring only first-generation Pokemon."
        }
        return descriptions.get(theme, "Custom team recommendation.")

# Convenience functions
def load_data(file_path=None, data=None):
    """Load Pokemon dataset"""
    analytics = PokemonAnalytics()
    return analytics.load_data(file_path, data)

def get_analytics_instance(data=None, file_path=None):
    """Get a configured analytics instance"""
    analytics = PokemonAnalytics()
    if data is not None or file_path is not None:
        analytics.load_data(file_path, data)
        analytics.clean_data()
    return analytics