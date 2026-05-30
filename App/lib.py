import warnings
import logging
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.cluster import KMeans
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression, LinearRegression
from sklearn.metrics import accuracy_score, mean_squared_error, r2_score
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.model_selection import train_test_split, GridSearchCV, StratifiedKFold
from sklearn.preprocessing import LabelEncoder, StandardScaler
import ast
from App.ml_utils import (
    compute_data_hash,
    save_model,
    save_scalers_and_encoders,
    load_latest_model,
    load_scalers_and_encoders,
)

warnings.filterwarnings('ignore')
logger = logging.getLogger(__name__)


# ── Typed Exceptions ──
class PokemonAnalyticsError(Exception):
    """Base exception for analytics errors."""
    pass

class DataNotLoadedError(PokemonAnalyticsError):
    """Raised when data hasn't been loaded yet."""
    pass

class DataNotCleanedError(PokemonAnalyticsError):
    """Raised when data hasn't been cleaned yet."""
    pass

class ModelNotTrainedError(PokemonAnalyticsError):
    """Raised when models haven't been trained yet."""
    pass


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
            raise DataNotLoadedError("Either file_path or data must be provided")
        return self.data
    
    def clean_data(self):
        """
        Clean and preprocess the Pokemon dataset
        Returns:
            DataFrame: Cleaned data
        """
        if self.data is None:
            raise DataNotLoadedError("Data must be loaded first using load_data()")
        
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
        
        # Ensure numeric columns from CSV (capture_rate may have text like "30 (Meteorite)255 (Core)")
        df['capture_rate'] = pd.to_numeric(df['capture_rate'], errors='coerce').fillna(45).astype(int)
        df['base_total'] = pd.to_numeric(df['base_total'], errors='coerce').fillna(300).astype(int)
        
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
        return df.copy()
    
    def get_descriptive_stats(self):
        """
        Calculate descriptive statistics for Pokemon data
        Returns:
            dict: Dictionary containing various descriptive statistics
        """
        if self.cleaned_data is None:
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")

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
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")
        
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
    
    def train_predictive_models(self, model_params=None, force_retrain=False):
        """
        Train predictive models for Pokemon stats prediction.
        Uses cached models when data hasn't changed and force_retrain is False.

        Args:
            model_params (dict, optional): Override default model hyperparameters.
            force_retrain (bool): If True, ignore cache and retrain from scratch.

        Returns:
            dict: Performance metrics for both models.
        """
        if self.cleaned_data is None:
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")

        df = self.cleaned_data.copy()
        data_hash = compute_data_hash(df)

        # ── Attempt cache load ──
        if not force_retrain:
            cached_leg = load_latest_model('legendary_classifier', data_hash)
            cached_reg = load_latest_model('stats_regressor', data_hash)
            cached_scalers, cached_encoders = load_scalers_and_encoders(data_hash)

            if cached_leg and cached_reg and cached_scalers and cached_encoders:
                logger.info("Using cached models (data hash: %s)", data_hash)
                self.models['legendary_classifier'] = cached_leg
                self.models['stats_regressor'] = cached_reg
                self.scalers = cached_scalers
                self.encoders = cached_encoders

                # Load cached metrics from manifest
                from App.ml_utils import _load_manifest
                manifest = _load_manifest()
                results = {
                    'legendary_prediction': manifest.get('legendary_classifier', {}).get('metrics', {}),
                    'stats_prediction': manifest.get('stats_regressor', {}).get('metrics', {}),
                    'cached': True,
                    'data_hash': data_hash,
                }
                return results

            logger.info("Cache miss — training new models")

        # ── Full training pipeline ──
        # Encode categorical variables
        le_type1 = LabelEncoder()
        le_type2 = LabelEncoder()
        le_generation_group = LabelEncoder()

        df['type1_encoded'] = le_type1.fit_transform(df['type1'])
        df['type2_encoded'] = le_type2.fit_transform(df['type2'])
        df['generation_group_encoded'] = le_generation_group.fit_transform(df['generation_group'].astype(str))

        encoders = {
            'type1': le_type1,
            'type2': le_type2,
            'generation_group': le_generation_group
        }
        self.encoders = encoders

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

        scalers = {
            'legendary': scaler_legendary,
            'stats': scaler_stats
        }
        self.scalers = scalers

        results = {}

        # ── Train legendary prediction model (with GridSearchCV) ──
        # Use stratified K-fold since legendaries are only ~8.7% of data
        leg_param_grid = {
            'n_estimators': [50, 100, 200],
            'max_depth': [5, 10, 15, None],
            'min_samples_split': [2, 5, 10],
            'class_weight': ['balanced', 'balanced_subsample', None],
        }

        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        grid_leg = GridSearchCV(
            RandomForestClassifier(random_state=42),
            param_grid=leg_param_grid,
            cv=cv,
            scoring='f1',  # F1 over accuracy for imbalanced data
            n_jobs=-1,
            verbose=0,
        )
        grid_leg.fit(X_train_leg, y_train_leg)
        rf_classifier = grid_leg.best_estimator_
        y_pred_leg = rf_classifier.predict(X_test_leg)

        legendary_accuracy = accuracy_score(y_test_leg, y_pred_leg)
        legendary_f1 = grid_leg.best_score_

        # Feature importance for legendary prediction
        feature_names_leg = feature_cols + ['base_total']
        leg_importance = {name: float(imp) for name, imp in
                         zip(feature_names_leg, rf_classifier.feature_importances_)}

        self.models['legendary_classifier'] = rf_classifier
        leg_metrics = {
            'accuracy': float(legendary_accuracy),
            'f1_cv_score': float(legendary_f1),
            'best_params': grid_leg.best_params_,
            'feature_importance': leg_importance,
            'model_type': 'RandomForest',
        }
        results['legendary_prediction'] = leg_metrics
        logger.info(
            "Legendary classifier: F1=%.4f, accuracy=%.4f, best_params=%s",
            legendary_f1, legendary_accuracy, grid_leg.best_params_,
        )

        # ── Ensemble: Gradient Boosting + Logistic Regression + Voting ──
        from sklearn.ensemble import GradientBoostingClassifier, VotingClassifier
        from sklearn.linear_model import LogisticRegression

        # Gradient Boosting
        gb = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
        gb.fit(X_train_leg, y_train_leg)
        y_pred_gb = gb.predict(X_test_leg)
        gb_accuracy = accuracy_score(y_test_leg, y_pred_gb)

        # Logistic Regression (scaled)
        lr = LogisticRegression(C=1.0, max_iter=1000, random_state=42, class_weight='balanced')
        lr.fit(X_train_leg_scaled, y_train_leg)
        y_pred_lr = lr.predict(X_test_leg_scaled)
        lr_accuracy = accuracy_score(y_test_leg, y_pred_lr)

        # Voting Classifier (soft voting — averages probabilities)
        voting = VotingClassifier(
            estimators=[
                ('rf', RandomForestClassifier(random_state=42, class_weight='balanced', n_estimators=100, max_depth=10)),
                ('gb', GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)),
                ('lr', LogisticRegression(C=1.0, max_iter=1000, random_state=42, class_weight='balanced')),
            ],
            voting='soft',
        )
        voting.fit(X_train_leg, y_train_leg)
        y_pred_voting = voting.predict(X_test_leg)
        voting_accuracy = accuracy_score(y_test_leg, y_pred_voting)

        # Store ensemble models
        self.models['legendary_gb'] = gb
        self.models['legendary_lr'] = lr
        self.models['legendary_voting'] = voting

        results['ensemble_comparison'] = {
            'random_forest': {
                'accuracy': float(legendary_accuracy),
                'f1_cv_score': float(legendary_f1),
            },
            'gradient_boosting': {
                'accuracy': float(gb_accuracy),
            },
            'logistic_regression': {
                'accuracy': float(lr_accuracy),
            },
            'voting_ensemble': {
                'accuracy': float(voting_accuracy),
            },
        }
        logger.info(
            "Ensemble accuracies — RF: %.4f, GB: %.4f, LR: %.4f, Voting: %.4f",
            legendary_accuracy, gb_accuracy, lr_accuracy, voting_accuracy,
        )

        # ── Train stats prediction model ──
        # Simpler param grid for the regressor
        stats_param_grid = {
            'n_estimators': [50, 100, 200],
            'max_depth': [5, 10, 15, None],
            'min_samples_split': [2, 5],
        }
        grid_stats = GridSearchCV(
            RandomForestRegressor(random_state=42),
            param_grid=stats_param_grid,
            cv=3,  # 3-fold since this is regression with fewer combos
            scoring='r2',
            n_jobs=-1,
            verbose=0,
        )
        grid_stats.fit(X_train_stats, y_train_stats)
        rf_regressor = grid_stats.best_estimator_
        y_pred_stats = rf_regressor.predict(X_test_stats)

        stats_r2 = r2_score(y_test_stats, y_pred_stats)
        stats_mse = mean_squared_error(y_test_stats, y_pred_stats)

        # Feature importance for stats prediction
        feature_names_stats = feature_cols[:-1]
        stats_importance = {name: float(imp) for name, imp in
                          zip(feature_names_stats, rf_regressor.feature_importances_)}

        self.models['stats_regressor'] = rf_regressor
        stats_metrics = {
            'r2_score': float(stats_r2),
            'mse': float(stats_mse),
            'best_params': grid_stats.best_params_,
            'feature_importance': stats_importance,
        }
        results['stats_prediction'] = stats_metrics
        results['cached'] = False
        results['data_hash'] = data_hash

        # ── Persist to cache ──
        try:
            save_model(rf_classifier, 'legendary_classifier', data_hash, leg_metrics)
            save_model(rf_regressor, 'stats_regressor', data_hash, stats_metrics)
            save_scalers_and_encoders(scalers, encoders, data_hash)
            logger.info("Models cached for data hash %s", data_hash)
        except Exception as e:
            logger.warning("Failed to cache models: %s", e)

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
            raise ModelNotTrainedError("Models must be trained first using train_predictive_models()")
        
        # Create DataFrame from input
        df = pd.DataFrame([pokemon_data])
        
        # Apply encodings with fallback for unseen types
        try:
            df['type1_encoded'] = self.encoders['type1'].transform(df['type1'])
        except ValueError:
            logger.warning("Unseen type1 value, defaulting to most common type")
            type1_fallback = self.cleaned_data['type1'].mode()[0]
            df['type1'] = type1_fallback
            df['type1_encoded'] = self.encoders['type1'].transform(df['type1'])
        
        try:
            df['type2_encoded'] = self.encoders['type2'].transform(df['type2'])
        except ValueError:
            logger.warning("Unseen type2 value, defaulting to 'None'")
            df['type2'] = 'None'
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
                'height_m', 'weight_kg', 'capture_rate'
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
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")
        df = self.cleaned_data.copy()
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

    def perform_clustering_with_viz(self, n_clusters=5, viz_method='pca'):
        """
        Perform clustering and return 2D projection data for visualization.
        Args:
            n_clusters (int): Number of clusters
            viz_method (str): 'pca' or 'tsne'
        Returns:
            dict: Clustering results with visualization-ready point data
        """
        if self.cleaned_data is None:
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")

        df = self.cleaned_data.copy()

        le_type1 = LabelEncoder()
        df['type1_encoded'] = le_type1.fit_transform(df['type1'])

        clustering_features = [
            'hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed',
            'type1_encoded', 'generation', 'height_m', 'weight_kg'
        ]

        X = df[clustering_features].fillna(df[clustering_features].median())

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Cluster
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init='auto')
        clusters = kmeans.fit_predict(X_scaled)

        # 2D projection
        if viz_method == 'tsne':
            from sklearn.manifold import TSNE
            reducer = TSNE(n_components=2, random_state=42, perplexity=30)
            coords = reducer.fit_transform(X_scaled)
            variance = None
        else:
            from sklearn.decomposition import PCA
            reducer = PCA(n_components=2, random_state=42)
            coords = reducer.fit_transform(X_scaled)
            variance = reducer.explained_variance_ratio_.tolist()

        # Build visualization data with a sample limit for performance
        max_points = min(500, len(df))
        sample_indices = np.random.RandomState(42).choice(len(df), max_points, replace=False)

        viz_data = []
        for i in sample_indices:
            row = df.iloc[i]
            viz_data.append({
                'name': row['name'],
                'x': float(coords[i, 0]),
                'y': float(coords[i, 1]),
                'cluster': int(clusters[i]),
                'type1': row['type1'],
                'type2': row['type2'] if row['type2'] != 'None' else None,
                'is_legendary': bool(row['is_legendary']),
                'base_total': int(row['base_total']),
                'pokedex_number': int(row['pokedex_number']),
            })

        # Analyze clusters
        cluster_analysis = {}
        for i in range(n_clusters):
            cluster_data = df[df['Cluster'] == i] if 'Cluster' in df else df[clusters == i]
            type_dist = {}
            if 'Cluster' not in df:
                df['Cluster'] = clusters
            cluster_data = df[df['Cluster'] == i]

            type_dist = cluster_data['type1'].value_counts().head(3).to_dict()
            cluster_analysis[f'cluster_{i}'] = {
                'size': int(len(cluster_data)),
                'legendary_rate': float(cluster_data['is_legendary'].mean()),
                'avg_base_total': float(cluster_data['base_total'].mean()),
                'dominant_types': {str(k): int(v) for k, v in type_dist.items()},
            }

        return {
            'visualization': viz_data,
            'total_clusters': n_clusters,
            'total_points': len(viz_data),
            'variance_explained': variance,
            'cluster_analysis': cluster_analysis,
        }

    def find_similar_pokemon(self, pokemon_name, n_similar=5, metric='cosine'):
        """
        Find the most similar Pokemon by stat profile using cosine similarity.
        
        Args:
            pokemon_name (str): Name of the Pokemon to compare against.
            n_similar (int): Number of similar Pokemon to return.
            metric (str): Similarity metric (currently only 'cosine').
            
        Returns:
            list[dict]: Similar Pokemon with similarity scores.
        """
        if self.cleaned_data is None:
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")

        df = self.cleaned_data

        # Partial match lookup
        matches = df[df['name'].str.contains(pokemon_name, case=False, na=False)]
        if matches.empty:
            raise PokemonAnalyticsError(f"No Pokemon found matching '{pokemon_name}'")
        target_name = matches.iloc[0]['name']

        feature_cols = ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed',
                        'base_total', 'height_m', 'weight_kg', 'capture_rate']

        scaler = StandardScaler()
        scaled = scaler.fit_transform(df[feature_cols].fillna(df[feature_cols].median()))

        query_idx = df[df['name'] == target_name].index[0]
        query_vec = scaled[query_idx].reshape(1, -1)

        similarities = cosine_similarity(query_vec, scaled)[0]

        # Exclude self, get top N
        similar_indices = similarities.argsort()[::-1]
        similar_indices = similar_indices[similar_indices != query_idx][:n_similar]

        results = []
        for idx in similar_indices:
            row = df.iloc[idx]
            results.append({
                'name': row['name'],
                'type1': row['type1'],
                'type2': row['type2'] if row['type2'] != 'None' else None,
                'base_total': int(row['base_total']),
                'similarity_score': round(float(similarities[idx]), 4),
                'stats': {
                    'hp': int(row['hp']),
                    'attack': int(row['attack']),
                    'defense': int(row['defense']),
                    'sp_attack': int(row['sp_attack']),
                    'sp_defense': int(row['sp_defense']),
                    'speed': int(row['speed']),
                }
            })

        return results

    def find_closest_pokemon(self, stats, type1='normal', type2=None, n_results=5):
        """
        Find the closest real Pokemon to a given stat profile (reverse search).
        Args:
            stats (dict): {hp, attack, defense, sp_attack, sp_defense, speed}
            type1 (str): Primary type
            type2 (str, optional): Secondary type
            n_results (int): Number of closest matches to return
        Returns:
            list[dict]: Closest Pokemon with similarity scores
        """
        if self.cleaned_data is None:
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")

        df = self.cleaned_data

        # Build a 1-row DataFrame from the input stats
        query_df = pd.DataFrame([{
            'hp': stats.get('hp', 85),
            'attack': stats.get('attack', 85),
            'defense': stats.get('defense', 85),
            'sp_attack': stats.get('sp_attack', 85),
            'sp_defense': stats.get('sp_defense', 85),
            'speed': stats.get('speed', 85),
            'base_total': sum(stats.get(k, 85) for k in ['hp','attack','defense','sp_attack','sp_defense','speed']),
            'height_m': 1.5,
            'weight_kg': 50.0,
            'capture_rate': 45,
            'type1': type1,
            'type2': type2 if type2 else 'None',
        }])

        feature_cols = ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed',
                        'base_total', 'height_m', 'weight_kg', 'capture_rate']

        scaler = StandardScaler()
        scaled_all = scaler.fit_transform(df[feature_cols].fillna(df[feature_cols].median()))
        scaled_query = scaler.transform(query_df[feature_cols].fillna(df[feature_cols].median()))

        similarities = cosine_similarity(scaled_query, scaled_all)[0]
        similar_indices = similarities.argsort()[::-1][:n_results]

        results = []
        for idx in similar_indices:
            row = df.iloc[idx]
            results.append({
                'name': row['name'],
                'pokedex_number': int(row['pokedex_number']),
                'type1': row['type1'],
                'type2': row['type2'] if row['type2'] != 'None' else None,
                'base_total': int(row['base_total']),
                'similarity': float(round(similarities[idx], 4)),
                'stats': {
                    'hp': int(row['hp']),
                    'attack': int(row['attack']),
                    'defense': int(row['defense']),
                    'sp_attack': int(row['sp_attack']),
                    'sp_defense': int(row['sp_defense']),
                    'speed': int(row['speed']),
                },
            })
        return results

    def optimize_pokemon_build(self, target_stat='base_total'):
        """
        Optimize Pokemon characteristics for maximum performance
        Args:
            target_stat (str): The stat to optimize for
        Returns:
            dict: Optimization results
        """
        if not self.models or 'stats_regressor' not in self.models:
            raise ModelNotTrainedError("Stats regression model must be trained first")
        
        model = self.models['stats_regressor']
        
        def pokemon_performance(params):
            # params: [hp, attack, defense, sp_attack, sp_defense, speed, type1_encoded, type2_encoded, generation, has_dual_type, height_m, weight_kg, capture_rate]
            features = np.array(params).reshape(1, -1)
            prediction = model.predict(features)[0]
            return -prediction  # Negative because we want to maximize
        
        # Define bounds for optimization (matches training features — no num_abilities)
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
        ]
        
        # Initial guess (balanced stats)
        x0 = [85, 85, 85, 85, 85, 85, 0, 1, 5, 1, 1.5, 50, 45]
        
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
            'predicted_base_total': round(max_performance, 2)
        }
        
        return optimal_pokemon

    def recommend_team(self, preferences=None, random_seed=None):
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
            random_seed (int, optional): Seed for deterministic random choices.
        Returns:
            dict: Recommended team with analysis
        """
        if self.cleaned_data is None:
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")
        
        # Handle case where no preferences are provided
        if preferences is None:
            preferences = {}
        
        rng = np.random.RandomState(random_seed) if random_seed is not None else np.random.RandomState(42)
        
        df = self.cleaned_data.copy()
        
        # Filter based on preferences
        filtered_df = df.copy()
        
        # Filter by legendary preference (default to include some legendaries but not dominated)
        include_legendary = preferences.get('include_legendary')
        if include_legendary is False:
            # Explicitly exclude legendaries
            filtered_df = filtered_df[filtered_df['is_legendary'] == False].copy()
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
                ].copy()
        
        # Filter by preferred types
        if 'preferred_types' in preferences and preferences['preferred_types']:
            type_filter = filtered_df['type1'].isin(preferences['preferred_types']) | \
                         filtered_df['type2'].isin(preferences['preferred_types'])
            filtered_df = filtered_df[type_filter].copy()
        
        # Define team roles and selection criteria based on playstyle
        playstyle = preferences.get('playstyle', 'balanced')
        
        if playstyle == 'offensive':
            # High attack/sp_attack focus
            filtered_df = filtered_df.copy()
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
            
            # Apply legendary bias with a hard cap of max 2 legendaries per team
            include_legendary = preferences.get('include_legendary')
            legendary_count = sum(1 for p in recommended_team if p.get('is_legendary', False))
            if legendary_count >= 2:
                # Hard cap: no more than 2 legendaries
                available_df = available_df[available_df['is_legendary'] == False]
            elif include_legendary is not False and legendary_count == 0 and len(recommended_team) >= 3:
                # Possibly introduce a legendary if we haven't seen one yet
                legendary_candidates = available_df[available_df['is_legendary'] == True]
                if len(legendary_candidates) > 0 and rng.rand() < 0.3:
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
    
    def get_team_recommendations_by_theme(self, theme='competitive', random_seed=None):
        """
        Get pre-defined team recommendations based on popular themes
        Args:
            theme (str): Theme for team recommendation
                - 'competitive': Meta competitive team
                - 'starter_friendly': Good for beginners
                - 'legendary': Legendary-focused team
                - 'type_specialist': Single-type focused team
                - 'generation_classic': Classic generation 1 team
            random_seed (int, optional): Seed for deterministic random choices.
        Returns:
            dict: Themed team recommendation
        """
        if self.cleaned_data is None:
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")
        
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
            raise PokemonAnalyticsError(f"Unknown theme: {theme}. Available themes: {list(theme_preferences.keys())}")
        
        preferences = theme_preferences[theme]
        
        # For type specialist, pick a random strong type
        if theme == 'type_specialist':
            theme_rng = np.random.RandomState(random_seed) if random_seed is not None else np.random.RandomState(42)
            df = self.cleaned_data
            type_stats = df.groupby('type1')['base_total'].agg(['mean', 'count'])
            strong_types = type_stats[type_stats['count'] >= 10].nlargest(5, 'mean').index.tolist()
            selected_type = strong_types[theme_rng.randint(len(strong_types))]
            preferences.update({
                'playstyle': 'balanced',
                'preferred_types': [selected_type],
                'include_legendary': False,
                'difficulty_level': 'intermediate',
                'team_role_balance': False
            })
        
        result = self.recommend_team(preferences, random_seed=random_seed)
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

    def calculate_team_type_coverage(self, team_names):
        """
        Calculate type defense coverage for a team using against_* data.
        Args:
            team_names (list[str]): Pokemon names in the team.
        Returns:
            dict: Weaknesses, resistances, immunities, defense multipliers per type.
        """
        if self.cleaned_data is None:
            raise DataNotCleanedError("Data must be cleaned first using clean_data()")

        against_cols = [
            'against_bug', 'against_dark', 'against_dragon', 'against_electric',
            'against_fairy', 'against_fight', 'against_fire', 'against_flying',
            'against_ghost', 'against_grass', 'against_ground', 'against_ice',
            'against_normal', 'against_poison', 'against_psychic', 'against_rock',
            'against_steel', 'against_water',
        ]
        type_names = [col.replace('against_', '') for col in against_cols]
        # Fix naming: 'fight' -> 'fighting'
        type_names = ['fighting' if t == 'fight' else t for t in type_names]

        df = self.cleaned_data
        team_df = df[df['name'].isin(team_names)]
        if team_df.empty:
            return {'error': 'No Pokemon found in team list'}

        # Worst-case defense: each attacking type hits the weakest member
        worst_case = {}
        for col, tname in zip(against_cols, type_names):
            if col in team_df.columns:
                worst_case[tname] = float(team_df[col].max())
            else:
                worst_case[tname] = 1.0

        weaknesses = [t for t, v in worst_case.items() if v > 1.0]
        resistances = [t for t, v in worst_case.items() if v < 1.0 and v > 0.0]
        immunities = [t for t, v in worst_case.items() if v == 0.0]

        coverage_score = len(resistances) / max(len(type_names), 1)

        return {
            'worst_case': worst_case,
            'weaknesses': sorted(weaknesses),
            'resistances': sorted(resistances),
            'immunities': sorted(immunities),
            'coverage_score': round(coverage_score, 3),
            'weakness_count': len(weaknesses),
            'resistance_count': len(resistances),
            'immunity_count': len(immunities),
        }

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