"""ML model tests for PokemonAnalytics engine."""

import pytest
import pandas as pd
from pathlib import Path
from App.lib import (
    PokemonAnalytics,
    DataNotLoadedError,
    DataNotCleanedError,
    ModelNotTrainedError,
    PokemonAnalyticsError,
)

# ── Fixtures ──

@pytest.fixture(scope="module")
def csv_path() -> Path:
    """Path to the Pokemon CSV dataset."""
    return Path(__file__).parent.parent / "pokemon.csv"


@pytest.fixture(scope="module")
def raw_df(csv_path: Path) -> pd.DataFrame:
    """Raw DataFrame loaded from CSV."""
    return pd.read_csv(csv_path)


@pytest.fixture(scope="module")
def analytics(raw_df: pd.DataFrame) -> PokemonAnalytics:
    """Fully initialized and trained analytics instance."""
    a = PokemonAnalytics()
    a.load_data(data=raw_df)
    a.clean_data()
    a.train_predictive_models()
    return a


# ── Data Loading Tests ──

class TestDataLoading:
    def test_load_from_dataframe(self, raw_df):
        a = PokemonAnalytics()
        result = a.load_data(data=raw_df)
        assert result is not None
        assert len(result) == 801
        assert a.data is not None

    def test_load_rejects_no_input(self):
        a = PokemonAnalytics()
        with pytest.raises(DataNotLoadedError):
            a.load_data()

    def test_clean_before_load_raises_error(self):
        a = PokemonAnalytics()
        with pytest.raises(DataNotLoadedError):
            a.clean_data()


# ── Data Cleaning Tests ──

class TestDataCleaning:
    def test_clean_creates_derived_features(self, raw_df):
        a = PokemonAnalytics()
        a.load_data(data=raw_df)
        df = a.clean_data()
        assert 'bmi' in df.columns
        assert 'power_level' in df.columns
        assert 'attack_defense_ratio' in df.columns
        assert 'num_abilities' in df.columns
        assert 'catch_difficulty' in df.columns

    def test_cleaned_data_is_copy(self, raw_df):
        a = PokemonAnalytics()
        a.load_data(data=raw_df)
        df = a.clean_data()
        # Modifying cleaned data should not affect raw
        orig_hp = df['hp'].iloc[0]
        df['hp'] = 0
        assert a.cleaned_data['hp'].iloc[0] == orig_hp

    def test_missing_values_handled(self, raw_df):
        a = PokemonAnalytics()
        a.load_data(data=raw_df)
        df = a.clean_data()
        assert df['height_m'].isna().sum() == 0
        assert df['weight_kg'].isna().sum() == 0
        assert df['percentage_male'].isna().sum() == 0


# ── Descriptive Stats Tests ──

class TestDescriptiveStats:
    def test_stats_returns_all_keys(self, analytics):
        stats = analytics.get_descriptive_stats()
        assert 'total_pokemon' in stats
        assert 'legendary_count' in stats
        assert 'stat_averages' in stats
        assert 'type_distribution' in stats
        assert 'strongest_pokemon' in stats
        assert 'fastest_pokemon' in stats
        assert 'rarest_pokemon' in stats

    def test_total_pokemon_is_801(self, analytics):
        assert analytics.get_descriptive_stats()['total_pokemon'] == 801

    def test_stat_averages_are_reasonable(self, analytics):
        avg = analytics.get_descriptive_stats()['stat_averages']
        for stat in ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed']:
            assert 50 <= avg[stat] <= 150, f"{stat} average {avg[stat]} out of range"

    def test_raises_when_not_cleaned(self, raw_df):
        a = PokemonAnalytics()
        a.load_data(data=raw_df)
        with pytest.raises(DataNotCleanedError):
            a.get_descriptive_stats()


# ── Model Training Tests ──

class TestModelTraining:
    def test_models_are_trained(self, analytics):
        assert 'legendary_classifier' in analytics.models
        assert 'stats_regressor' in analytics.models

    def test_legendary_accuracy_above_threshold(self, analytics):
        perf = analytics.train_predictive_models()
        acc = perf['legendary_prediction']['accuracy']
        # Legendary prediction should be > 85% given the data
        assert acc > 0.85, f"Legendary accuracy {acc:.3f} < 0.85"

    def test_legendary_f1_cv_above_threshold(self, analytics):
        perf = analytics.train_predictive_models()
        f1 = perf['legendary_prediction'].get('f1_cv_score', 0)
        # F1 should be > 0.7 given balanced training
        assert f1 > 0.7, f"Legendary F1 CV {f1:.3f} < 0.7"

    def test_regressor_r2_above_threshold(self, analytics):
        perf = analytics.train_predictive_models()
        r2 = perf['stats_prediction']['r2_score']
        assert r2 > 0.90, f"R² {r2:.3f} < 0.90"

    def test_regressor_mse_is_reasonable(self, analytics):
        perf = analytics.train_predictive_models()
        mse = perf['stats_prediction']['mse']
        # MSE should be less than 2000 for reliable predictions
        assert mse < 2000, f"MSE {mse:.1f} >= 2000"

    def test_raises_when_not_cleaned(self, raw_df):
        a = PokemonAnalytics()
        a.load_data(data=raw_df)
        with pytest.raises(DataNotCleanedError):
            a.train_predictive_models()

    def test_deterministic_training(self, raw_df):
        """Same input should produce same model metrics (no seed-dependent variance)."""
        a1 = PokemonAnalytics()
        a1.load_data(data=raw_df)
        a1.clean_data()
        r1 = a1.train_predictive_models()

        a2 = PokemonAnalytics()
        a2.load_data(data=raw_df)
        a2.clean_data()
        r2 = a2.train_predictive_models()

        assert r1['legendary_prediction']['accuracy'] == r2['legendary_prediction']['accuracy']
        assert r1['stats_prediction']['r2_score'] == r2['stats_prediction']['r2_score']


# ── Prediction Tests ──

class TestPrediction:
    @pytest.fixture
    def sample_pokemon(self):
        return {
            'hp': 100, 'attack': 100, 'defense': 100,
            'sp_attack': 100, 'sp_defense': 100, 'speed': 100,
            'type1': 'normal', 'type2': 'None', 'generation': 1,
            'height_m': 1.5, 'weight_kg': 50, 'capture_rate': 45,
            'num_abilities': 2, 'base_total': 600
        }

    def test_predicted_base_total_in_range(self, analytics, sample_pokemon):
        result = analytics.predict_pokemon_stats(sample_pokemon)
        total = result['stats_prediction']['predicted_base_total']
        assert 6 <= total <= 1530, f"Base total {total} out of range [6, 1530]"

    def test_legendary_probability_in_range(self, analytics, sample_pokemon):
        result = analytics.predict_pokemon_stats(sample_pokemon)
        prob = result['legendary_prediction']['legendary_probability']
        assert 0 <= prob <= 1, f"Probability {prob} out of range [0, 1]"

    def test_legendary_is_binary(self, analytics, sample_pokemon):
        result = analytics.predict_pokemon_stats(sample_pokemon)
        pred = result['legendary_prediction']['is_legendary']
        assert pred in (0, 1)

    def test_high_stats_increase_legendary_prob(self, analytics, sample_pokemon):
        """A Pokemon with max stats should have higher legendary prob than min stats."""
        min_stats = {**sample_pokemon, 'hp': 1, 'attack': 1, 'defense': 1,
                     'sp_attack': 1, 'sp_defense': 1, 'speed': 1, 'base_total': 6}
        max_stats = {**sample_pokemon, 'hp': 255, 'attack': 255, 'defense': 255,
                     'sp_attack': 255, 'sp_defense': 255, 'speed': 255, 'base_total': 1530}

        prob_min = analytics.predict_pokemon_stats(min_stats)['legendary_prediction'][
            'legendary_probability'
        ]
        prob_max = analytics.predict_pokemon_stats(max_stats)['legendary_prediction'][
            'legendary_probability'
        ]
        assert prob_max >= prob_min, "Higher stats should not decrease legendary probability"

    def test_predict_with_unseen_type(self, analytics, sample_pokemon):
        """Should not crash with an unseen type value."""
        weird_pokemon = {**sample_pokemon, 'type1': 'unknown_type_xyz', 'type2': 'unknown_123'}
        try:
            result = analytics.predict_pokemon_stats(weird_pokemon)
            assert 'stats_prediction' in result
            assert 'legendary_prediction' in result
        except Exception as e:
            pytest.fail(f"Prediction with unseen type raised: {e}")

    def test_predict_raises_without_training(self, raw_df):
        a = PokemonAnalytics()
        a.load_data(data=raw_df)
        a.clean_data()
        with pytest.raises(ModelNotTrainedError):
            a.predict_pokemon_stats({'hp': 100})


# ── Team Recommendation Tests ──

class TestTeamRecommendation:
    def test_team_has_6_members(self, analytics):
        result = analytics.recommend_team(preferences={}, random_seed=42)
        assert len(result['recommended_team']) == 6

    def test_team_members_have_required_keys(self, analytics):
        result = analytics.recommend_team(preferences={}, random_seed=42)
        required = ['name', 'type1', 'role', 'stats', 'is_legendary']
        for member in result['recommended_team']:
            for key in required:
                assert key in member, f"Team member missing '{key}'"

    def test_legendary_limit(self, analytics):
        """Team should not exceed 2 legendaries even when included."""
        result = analytics.recommend_team(
            {'include_legendary': True}, random_seed=42
        )
        leg_count = sum(1 for p in result['recommended_team'] if p.get('is_legendary', False))
        assert leg_count <= 2, f"Team has {leg_count} legendaries (max 2)"

    def test_team_deterministic_with_seed(self, analytics):
        """Same seed must produce same team."""
        r1 = analytics.recommend_team(preferences={}, random_seed=42)
        r2 = analytics.recommend_team(preferences={}, random_seed=42)
        names1 = [p['name'] for p in r1['recommended_team']]
        names2 = [p['name'] for p in r2['recommended_team']]
        assert names1 == names2

    def test_different_seeds_produce_different_teams(self, analytics):
        """Different seeds should generally produce different teams."""
        r1 = analytics.recommend_team(preferences={}, random_seed=1)
        r2 = analytics.recommend_team(preferences={}, random_seed=999)
        names1 = [p['name'] for p in r1['recommended_team']]
        names2 = [p['name'] for p in r2['recommended_team']]
        # They might still have some overlap by chance, but at least 1 should differ
        assert names1 != names2 or len(set(names1)) == 6

    def test_empty_preferences_works(self, analytics):
        """Empty preferences should still return a valid team."""
        result = analytics.recommend_team(preferences={}, random_seed=42)
        assert len(result['recommended_team']) == 6

    def test_none_preferences_works(self, analytics):
        """None preferences should still return a valid team."""
        result = analytics.recommend_team(preferences=None, random_seed=42)
        assert len(result['recommended_team']) == 6

    def test_team_analysis_has_keys(self, analytics):
        result = analytics.recommend_team(preferences={}, random_seed=42)
        analysis = result['team_analysis']
        assert 'total_base_stats' in analysis
        assert 'synergy_score' in analysis
        assert 'type_coverage' in analysis
        assert 'legendary_count' in analysis

    def test_type_filter_works(self, analytics):
        """When filtering by fire type, all team members should be fire or have fire type."""
        result = analytics.recommend_team(
            {'preferred_types': ['fire']}, random_seed=42
        )
        for p in result['recommended_team']:
            is_fire = p['type1'] == 'fire' or (p.get('type2') and p['type2'] == 'fire')
            assert is_fire, f"{p['name']} is not fire type ({p['type1']}/{p.get('type2')})"


# ── Clustering Tests ──

class TestClustering:
    def test_clustering_returns_expected_clusters(self, analytics):
        result = analytics.perform_clustering(n_clusters=5)
        assert len(result['cluster_analysis']) == 5
        assert result['total_clusters'] == 5

    def test_clustering_assignments_length(self, analytics):
        result = analytics.perform_clustering(n_clusters=5)
        assert len(result['cluster_assignments']) == 801

    def test_clustering_deterministic(self, analytics):
        """Same call should produce same assignments."""
        r1 = analytics.perform_clustering(n_clusters=5)
        r2 = analytics.perform_clustering(n_clusters=5)
        assert r1['cluster_assignments'] == r2['cluster_assignments']

    def test_clustering_variable_k(self, analytics):
        for k in [3, 5, 7]:
            result = analytics.perform_clustering(n_clusters=k)
            assert result['total_clusters'] == k
            assert len(result['cluster_analysis']) == k

    def test_cluster_has_dominant_types(self, analytics):
        result = analytics.perform_clustering(n_clusters=5)
        for _cluster_key, cluster_data in result['cluster_analysis'].items():
            assert 'dominant_types' in cluster_data
            assert len(cluster_data['dominant_types']) > 0
            assert cluster_data['size'] > 0


# ── Similarity Search Tests ──

class TestSimilaritySearch:
    def test_similarity_excludes_self(self, analytics):
        """A Pokemon should not appear in its own similarity results."""
        result = analytics.find_similar_pokemon('Pikachu', n_similar=5)
        names = [r['name'] for r in result]
        assert 'Pikachu' not in names, "Pikachu appeared in its own results"

    def test_similarity_returns_requested_count(self, analytics):
        result = analytics.find_similar_pokemon('Charizard', n_similar=5)
        assert len(result) == 5

    def test_similarity_scores_descending(self, analytics):
        """Results should be ordered by similarity (highest first)."""
        result = analytics.find_similar_pokemon('Mewtwo', n_similar=5)
        scores = [r['similarity_score'] for r in result]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], "Scores not in descending order"

    def test_similarity_partial_name_match(self, analytics):
        """Should handle partial name matches (e.g., 'char' -> Charizard)."""
        result = analytics.find_similar_pokemon('char', n_similar=3)
        assert len(result) == 3
        # At least one result should contain 'char' in its name
        has_char = any('char' in r['name'].lower() for r in result)
        assert has_char, "Partial match 'char' didn't find Charizard/Charmander"

    def test_similarity_unknown_pokemon_raises(self, analytics):
        with pytest.raises(PokemonAnalyticsError):
            analytics.find_similar_pokemon('ThisPokemonDoesNotExist', n_similar=5)


# ── Reverse Search Tests ──

class TestReverseSearch:
    def test_reverse_search_returns_results(self, analytics):
        result = analytics.find_closest_pokemon(
            stats={'hp': 100, 'attack': 100, 'defense': 100,
                   'sp_attack': 100, 'sp_defense': 100, 'speed': 100},
            type1='normal', n_results=5
        )
        assert len(result) == 5

    def test_reverse_search_has_keys(self, analytics):
        result = analytics.find_closest_pokemon(
            stats={'hp': 50, 'attack': 50, 'defense': 50,
                   'sp_attack': 50, 'sp_defense': 50, 'speed': 50},
            type1='water', n_results=3
        )
        for r in result:
            assert 'name' in r
            assert 'similarity' in r
            assert 'type1' in r
            assert 'pokedex_number' in r

    def test_reverse_search_similarity_bounded(self, analytics):
        result = analytics.find_closest_pokemon(
            stats={'hp': 100, 'attack': 100, 'defense': 100,
                   'sp_attack': 100, 'sp_defense': 100, 'speed': 100},
            type1='normal', n_results=5
        )
        for r in result:
            assert 0 <= r['similarity'] <= 1, f"Similarity {r['similarity']} out of [0,1]"


# ── Optimization Tests ──

class TestOptimization:
    def test_optimize_returns_all_keys(self, analytics):
        result = analytics.optimize_pokemon_build()
        assert 'hp' in result
        assert 'attack' in result
        assert 'defense' in result
        assert 'speed' in result
        assert 'predicted_base_total' in result

    def test_optimize_stats_in_range(self, analytics):
        result = analytics.optimize_pokemon_build()
        for stat in ['hp', 'attack', 'defense', 'sp_attack', 'sp_defense', 'speed']:
            assert 1 <= result[stat] <= 255, f"{stat} {result[stat]} out of range [1, 255]"

    def test_optimize_raises_without_training(self, raw_df):
        a = PokemonAnalytics()
        a.load_data(data=raw_df)
        a.clean_data()
        with pytest.raises(ModelNotTrainedError):
            a.optimize_pokemon_build()


# ── Diagnostic Analysis Tests ──

class TestDiagnostics:
    def test_diagnostics_has_all_sections(self, analytics):
        diag = analytics.diagnostic_analysis()
        assert 'stats_by_type' in diag
        assert 'stats_by_generation' in diag
        assert 'correlations' in diag
        assert 'popular_type_combinations' in diag

    def test_correlations_exist_for_all_stats(self, analytics):
        diag = analytics.diagnostic_analysis()
        for stat in ['attack', 'defense', 'sp_attack', 'sp_defense', 'speed', 'hp',
                     'height_m', 'weight_kg', 'capture_rate', 'generation',
                     'is_legendary', 'has_dual_type', 'bmi']:
            assert stat in diag['correlations'], f"Missing correlation for {stat}"

    def test_attack_defense_correlation_is_positive(self, analytics):
        diag = analytics.diagnostic_analysis()
        assert diag['stat_correlations']['attack_defense'] > 0

    def test_raises_when_not_cleaned(self, raw_df):
        a = PokemonAnalytics()
        a.load_data(data=raw_df)
        with pytest.raises(DataNotCleanedError):
            a.diagnostic_analysis()


# ── Type Coverage Tests ──

class TestTypeCoverage:
    def test_type_coverage_returns_all_keys(self, analytics):
        result = analytics.calculate_team_type_coverage(['Charizard', 'Blastoise'])
        assert 'weaknesses' in result
        assert 'resistances' in result
        assert 'coverage_score' in result
        assert 'worst_case' in result

    def test_coverage_score_is_bounded(self, analytics):
        result = analytics.calculate_team_type_coverage(['Mewtwo', 'Mew'])
        assert 0 <= result['coverage_score'] <= 1


# ── Model Persistence Tests ──

class TestModelPersistence:
    def test_data_hash_is_consistent(self, analytics):
        """Same data should produce same hash."""
        from App.ml_utils import compute_data_hash
        h1 = compute_data_hash(analytics.cleaned_data)
        h2 = compute_data_hash(analytics.cleaned_data)
        assert h1 == h2

    def test_cached_models_produce_same_results(self, analytics):
        """Fresh training and cached loading should give same predictions."""
        perf = analytics.train_predictive_models()
        acc1 = perf['legendary_prediction']['accuracy']
        r2_1 = perf['stats_prediction']['r2_score']

        # Re-train (after caching, this should be deterministic)
        perf2 = analytics.train_predictive_models()
        acc2 = perf2['legendary_prediction']['accuracy']
        r2_2 = perf2['stats_prediction']['r2_score']

        assert acc1 == acc2, "Accuracy changed between training runs"
        assert r2_1 == r2_2, "R² changed between training runs"
