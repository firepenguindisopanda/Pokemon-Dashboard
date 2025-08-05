import os, csv
import datetime
from flask import Flask, request, redirect, render_template, url_for, flash, jsonify
from flask_cors import CORS
from sqlalchemy.exc import IntegrityError
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    set_access_cookies,
    unset_jwt_cookies,
    current_user
)
from App.models import db, User, UserPokemon, Pokemon
from App.lib import PokemonAnalytics, get_analytics_instance
import pandas as pd

# Configure Flask App
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.root_path, 'data.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'MySecretKey'
app.config['JWT_ACCESS_COOKIE_NAME'] = 'access_token'
app.config['JWT_REFRESH_COOKIE_NAME'] = 'refresh_token'
app.config["JWT_TOKEN_LOCATION"] = ["cookies", "headers"]
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = datetime.timedelta(hours=15)
app.config["JWT_COOKIE_SECURE"] = True
app.config["JWT_SECRET_KEY"] = "super-secret"
app.config["JWT_COOKIE_CSRF_PROTECT"] = False
app.config['JWT_HEADER_NAME'] = "Cookie"


# Initialize App 
db.init_app(app)
app.app_context().push()
CORS(app)
jwt = JWTManager(app)

type_colors = {
"grass": "#78C850",
"fire": "#F08030",
"water": "#6890F0",
"bug": "#A8B820",
"normal": "#A8A878",
"poison": "#A040A0",
"electric": "#F8D030",
"ground": "#E0C068",
"fairy": "#EE99AC",
"fighting": "#C03028",
"psychic": "#F85888",
"rock": "#B8A038",
"ghost": "#705898",
"ice": "#98D8D8",
"dragon": "#7038F8",
"flying": "#A890F0",
"steel": "#B8B8D0",
"dark": "#705848"
}

# JWT Config to enable current_user
@jwt.user_identity_loader
def user_identity_lookup(user):
  return user.id

@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data):
  identity = jwt_data["sub"]
  return db.session.get(User, identity)

# *************************************

# Initializer Function to be used in both init command and /init route
# Parse pokemon.csv and populate database and creates user "bob" with password "bobpass"
def initialize_db():
  db.drop_all()
  db.create_all()
  with open('pokemon.csv', newline='', encoding='utf8') as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
      if row['height_m'] == '':
        row['height_m'] = None
      if row['weight_kg'] == '':
        row['weight_kg'] = None
      if row['type2'] == '':
        row['type2'] = None

      abilities_list = eval(row['abilities'])
      
      # Handle complex capture_rate values like "30 (Meteorite)255 (Core)"
      capture_rate_str = row['capture_rate'].strip()
      if '(' in capture_rate_str:
        # Extract the first number before any parentheses
        capture_rate = int(capture_rate_str.split('(')[0].strip())
      else:
        capture_rate = int(capture_rate_str)

      # Create the Pokemon object and associate it with the abilities
      pokemon = Pokemon(
                name=row['name'], 
                pokedex_number=row['pokedex_number'], 
                attack=row['attack'], 
                defense=row['defense'], 
                sp_attack=row['sp_attack'], 
                sp_defense=row['sp_defense'], 
                weight=row['weight_kg'], 
                height=row['height_m'], 
                hp=row['hp'], 
                speed=row['speed'], 
                type1=row['type1'], 
                type2=row['type2'], 
                generation=row['generation'], 
                classification=row['classification'],
                abilities=','.join(abilities_list),
                capture_rate=capture_rate,
                is_legendary=int(row['is_legendary']),
                percentage_male=float(row['percentage_male']) if row['percentage_male'] else 50.0,
                base_total=int(row['base_total'])
            )
      db.session.add(pokemon)

    bob = User(username='bob', email="bob@mail.com", password="bobpass")
    nick = User(username='nick', email="nick@mail.com", password="nickpass")
    db.session.add(bob)
    db.session.add(nick)
    db.session.commit()
    bob.catch_pokemon(1, "Benny")
    bob.catch_pokemon(25, "Saul")
    nick.catch_pokemon(120, 'Buddy')

def login_user(username, password):
  user = User.query.filter_by(username=username).first()
  if user and user.check_password(password):
    token = create_access_token(identity=user)
    return token
  return None

def get_pokemon_list():
  all_pokemon = [pokemens.get_json() for pokemens in Pokemon.query.all()]
  return all_pokemon

def search_pokemon_by_name(query):
  matching_pokemon = Pokemon.query.filter(Pokemon.name.ilike(f'%{query}%')).all()
  results = [pokemon.get_json() for pokemon in matching_pokemon]
  return results

def filter_pokemon_by_generation(generation):
  matching_pokemon = Pokemon.query.filter_by(generation=generation).all()
  results = [pokemon.get_json() for pokemon in matching_pokemon]
  return results


# ********** Routes **************

# Template implementation (don't change)

@app.route('/init')
def init_route():
  initialize_db()
  return redirect(url_for('login_page'))

@app.route("/", methods=['GET'])
def login_page():
  return render_template("login.html")

@app.route("/signup", methods=['GET'])
def signup_page():
    return render_template("signup.html")

@app.route("/signup", methods=['POST'])
def signup_action():
  response = None
  try:
    username = request.form['username']
    email = request.form['email']
    password = request.form['password']
    user = User(username=username, email=email, password=password)
    db.session.add(user)
    db.session.commit()
    response = redirect(url_for('home_page'))
    token = create_access_token(identity=user)
    set_access_cookies(response, token)
  except IntegrityError:
    flash('Username already exists')
    response = redirect(url_for('signup_page'))
  flash('Account created')
  return response

@app.route("/logout", methods=['GET'])
@jwt_required()
def logout_action():
  response = redirect(url_for('login_page'))
  unset_jwt_cookies(response)
  flash('Logged out')
  return response

# *************************************

@app.route("/search", methods=['GET'])
@jwt_required()
def search_pokemon():
  query = request.args.get('query', '')  # Get the search query from the URL parameters
  if not query:
    return redirect(url_for('pokemon_area'))  # If the query is empty, redirect to the home page

  # Perform the search based on the query
  results = search_pokemon_by_name(query)

  return render_template("pokemon_area.html", list_of_pokemon=results)

@app.route("/app", methods=['GET'])
@app.route("/app/<int:pokemon_id>", methods=['GET'])
@jwt_required()
def home_page(pokemon_id=1):
  query = request.args.get('query', '')  # Get the search query from the URL parameters
  # If there's a search query, perform the search and update the list of Pokémon
  if query:
    list_of_pokemon = search_pokemon_by_name(query)
  else:
    list_of_pokemon = get_pokemon_list()
  # update pass relevant data to template
  pokemon = db.session.get(Pokemon, pokemon_id).get_json()
  user_pokemons = UserPokemon.query.filter_by(user_id=current_user.get_json()['id']).all()
  user_pokemons_objects = [user_pokemon.get_json() for user_pokemon in user_pokemons]
  
  return render_template("home.html", list_of_pokemon=list_of_pokemon, selected_pokemon_id=pokemon_id, pokemon=pokemon, usr_pkmons=user_pokemons_objects)

@app.route("/pokemon-area", methods=['GET'])
@jwt_required()
def pokemon_area():
  query = request.args.get('query', '')  # Get the search query from the URL parameters
  selected_generation = request.args.get('generation', '')  # Get the selected generation
  all_pokemon = get_pokemon_list()
  if query:
    list_of_pokemon = search_pokemon_by_name(query)
  elif selected_generation:
    list_of_pokemon = filter_pokemon_by_generation(selected_generation)
  else:
    list_of_pokemon = get_pokemon_list()

  unique_generations = set(pokemon['generation'] for pokemon in all_pokemon)
  unique_generations_list = list(unique_generations)
  return render_template("pokemon_area.html", list_of_pokemon=list_of_pokemon, unique_generations_list=unique_generations_list, selected_generation=selected_generation)

@app.route("/pokemon-area/pokemon-details/<int:pokemon_id>", methods=['GET', "POST"])
@jwt_required()
def pokemon_area_details(pokemon_id=None):
  print(pokemon_id)
  pokemon_to_display_details = db.session.get(Pokemon, pokemon_id).get_json()
  return render_template("pokemon_area_details.html", current_user=current_user, pokemon=pokemon_to_display_details)

@app.route("/login", methods=['POST'])
def login_action():
  # implement login
  data = request.form
  token = login_user(data['username'], data['password'])
  print(token)
  response = None
  if token:
    flash('Logged in successfully.')  # send message to next page
    response = redirect(url_for('home_page'))  # redirect to main page if login successful
    set_access_cookies(response, token)
  else:
    flash('Invalid username or password')  # send message to next page
    response = redirect(url_for('login_page'))
  return response

@app.route("/pokemon/<int:pokemon_id>", methods=['POST'])
@jwt_required()
def capture_action(pokemon_id):
  # Get the current user from the JWT token
  current_user_id = current_user.id

  # Retrieve the provided nickname from the form data
  nickname = request.form.get('nickname')

  # Check if the Pokémon is already captured by the user
  if UserPokemon.query.filter_by(user_id=current_user_id, pokemon_id=pokemon_id).first():
    flash('You already captured this Pokémon!')
  else:
    # Capture the Pokémon for the user with the provided nickname
    current_user.catch_pokemon(pokemon_id, nickname)
    flash('Successfully captured the Pokémon!')

  return redirect(request.referrer)

@app.route("/rename-pokemon/<int:pokemon_id>", methods=['POST'])
@jwt_required()
def rename_action(pokemon_id):
  # Retrieve the new name from the form data
  print('Pokemon Id: ', pokemon_id)
  form_id = 'new_name_' + str(pokemon_id)
  new_name = request.form.get(form_id)
  user_pokemon = db.session.get(UserPokemon, pokemon_id)
  
  print('Specific Pokemon: ', user_pokemon.id)
  print('New Name: ', new_name)
  if current_user.rename_pokemon(user_pokemon.id, new_name):
    message = "Your Pokemon " + user_pokemon.get_json()['species'] + " has been given a new successfully!"
    flash(message)
  else:
    flash("We encountered an error while renaming your pokemon. Please make sure you correctly provided a name")

  return redirect(request.referrer)

# Global analytics instance (initialize once)
pokemon_analytics = None

def initialize_pokemon_analytics():
    """Initialize the analytics instance with your Pokemon data"""
    global pokemon_analytics
    try:
        all_pokemon = Pokemon.query.all()
        
        # Convert to DataFrame format
        pokemon_data = []
        for pokemon in all_pokemon:
            # Calculate num_abilities from abilities string
            abilities_count = len(pokemon.abilities.split(',')) if pokemon.abilities else 1
            
            pokemon_dict = {
                'name': pokemon.name,
                'pokedex_number': pokemon.pokedex_number,
                'hp': pokemon.hp,
                'attack': pokemon.attack,
                'defense': pokemon.defense,
                'sp_attack': pokemon.sp_attack,
                'sp_defense': pokemon.sp_defense,
                'speed': pokemon.speed,
                'base_total': pokemon.base_total,
                'type1': pokemon.type1,
                'type2': pokemon.type2 if pokemon.type2 else 'None',
                'generation': pokemon.generation,
                'height_m': pokemon.height if pokemon.height else 1.0,
                'weight_kg': pokemon.weight if pokemon.weight else 10.0,
                'classification': pokemon.classification,
                'abilities': pokemon.abilities,
                'capture_rate': pokemon.capture_rate,
                'is_legendary': pokemon.is_legendary,
                'percentage_male': pokemon.percentage_male,
                'num_abilities': abilities_count
            }
            pokemon_data.append(pokemon_dict)
        
        df = pd.DataFrame(pokemon_data)
        
        # Initialize analytics
        pokemon_analytics = PokemonAnalytics()
        pokemon_analytics.load_data(data=df)
        pokemon_analytics.clean_data()
        
        # Train models
        pokemon_analytics.train_predictive_models()
        
        return True
    except Exception as e:
        print(f"Error initializing analytics: {e}")
        return False

# Analytics Dashboard Routes

@app.route("/api/pokemon-analytics/stats", methods=['GET'])
@jwt_required()
def get_pokemon_descriptive_stats():
    """Get comprehensive descriptive statistics"""
    global pokemon_analytics
    if not pokemon_analytics:
        if not initialize_pokemon_analytics():
            return jsonify({"error": "Failed to initialize analytics"}), 500
    
    try:
        stats = pokemon_analytics.get_descriptive_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/pokemon-analytics/diagnostics", methods=['GET'])
@jwt_required()
def get_pokemon_diagnostics():
    """Get diagnostic analysis and correlations"""
    global pokemon_analytics
    if not pokemon_analytics:
        if not initialize_pokemon_analytics():
            return jsonify({"error": "Failed to initialize analytics"}), 500
    
    try:
        diagnostics = pokemon_analytics.diagnostic_analysis()
        return jsonify(diagnostics)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/pokemon-analytics/clustering", methods=['GET'])
@jwt_required()
def get_pokemon_clustering():
    """Perform clustering analysis"""
    global pokemon_analytics
    if not pokemon_analytics:
        if not initialize_pokemon_analytics():
            return jsonify({"error": "Failed to initialize analytics"}), 500
    
    try:
        n_clusters = request.args.get('clusters', 5, type=int)
        clustering_results = pokemon_analytics.perform_clustering(n_clusters)
        return jsonify(clustering_results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/pokemon-analytics/predict", methods=['POST'])
@jwt_required()
def predict_pokemon_performance():
    """Predict Pokemon stats and legendary status"""
    global pokemon_analytics
    if not pokemon_analytics:
        if not initialize_pokemon_analytics():
            return jsonify({"error": "Failed to initialize analytics"}), 500
    
    try:
        pokemon_data = request.json
        
        # Ensure required fields with defaults
        required_fields = {
            'hp': 50, 'attack': 50, 'defense': 50, 'sp_attack': 50, 
            'sp_defense': 50, 'speed': 50, 'type1': 'normal', 
            'type2': 'None', 'generation': 1, 'height_m': 1.0, 
            'weight_kg': 10.0, 'capture_rate': 45, 'num_abilities': 1
        }
        
        for field, default in required_fields.items():
            if field not in pokemon_data:
                pokemon_data[field] = default
        
        # Calculate base_total
        pokemon_data['base_total'] = (
            pokemon_data['hp'] + pokemon_data['attack'] + pokemon_data['defense'] +
            pokemon_data['sp_attack'] + pokemon_data['sp_defense'] + pokemon_data['speed']
        )
        
        predictions = pokemon_analytics.predict_pokemon_stats(pokemon_data)
        return jsonify(predictions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/pokemon-analytics/optimize", methods=['GET'])
@jwt_required()
def optimize_pokemon_build():
    """Get optimal Pokemon build recommendations"""
    global pokemon_analytics
    if not pokemon_analytics:
        if not initialize_pokemon_analytics():
            return jsonify({"error": "Failed to initialize analytics"}), 500
    
    try:
        optimal_build = pokemon_analytics.optimize_pokemon_build()
        return jsonify(optimal_build)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/pokemon-analytics/team-recommend", methods=['POST'])
@jwt_required()
def recommend_pokemon_team():
    """Get team recommendations based on user preferences"""
    global pokemon_analytics
    if not pokemon_analytics:
        if not initialize_pokemon_analytics():
            return jsonify({"error": "Failed to initialize analytics"}), 500
    
    try:
        # Handle case where no JSON body is provided
        preferences = request.json if request.json else {}
        
        # Validate preferences structure
        if not isinstance(preferences, dict):
            return jsonify({"error": "Invalid preferences format"}), 400
        
        # Log the received preferences for debugging
        print(f"Received team recommendation preferences: {preferences}")
        
        team_recommendations = pokemon_analytics.recommend_team(preferences)
        return jsonify(team_recommendations)
    except ValueError as e:
        # Handle specific validation errors
        return jsonify({"error": f"Validation error: {str(e)}"}), 400
    except Exception as e:
        # Handle general errors
        print(f"Error in team recommendation: {str(e)}")
        return jsonify({"error": f"Failed to generate team recommendation: {str(e)}"}), 500

@app.route("/api/pokemon-analytics/model-performance", methods=['GET'])
@jwt_required()
def get_model_performance():
    """Get model training results and performance metrics"""
    global pokemon_analytics
    if not pokemon_analytics:
        if not initialize_pokemon_analytics():
            return jsonify({"error": "Failed to initialize analytics"}), 500
    
    try:
        # Retrain to get fresh performance metrics
        performance = pokemon_analytics.train_predictive_models()
        return jsonify(performance)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Dashboard Route
@app.route("/pokemon-stats", methods=['GET'])
@jwt_required()
def pokemon_analytics_dashboard():
    """Render the analytics dashboard page"""
    global pokemon_analytics
    if not pokemon_analytics:
        initialize_pokemon_analytics()
    
    return render_template("pokemon_dashboard.html", type_colors=type_colors)

def get_combined_type_distribution():
  type_counts = {}
  all_pokemon = Pokemon.query.all()
    
  for pokemon in all_pokemon:
      types = [pokemon.type1]
      if pokemon.type2:
        types.append(pokemon.type2)
        
      for type_ in types:
        if type_ in type_counts:
          type_counts[type_] += 1
        else:
          type_counts[type_] = 1
    
  return type_counts

@app.route("/pokemon-stats-v1", methods=['GET'])
@jwt_required()
def pokemon_stats():
    # Retrieve Pokémon data for the charts
    pokemon_data = db.session.query(
        Pokemon.type1,
        db.func.count(Pokemon.id).label('count')
    ).group_by(Pokemon.type1).all()

    # Prepare data for the bar chart
    chart_data = {
        'labels': [data.type1 for data in pokemon_data],
        'values': [data.count for data in pokemon_data],
        'colors': [type_colors.get(data.type1, '#FFFFFF') for data in pokemon_data]
    }

    # Prepare data for the pie chart (percentage distribution)
    total_pokemon = sum(chart_data['values'])
    pie_chart_data = {
        'labels': chart_data['labels'],
        'values': [(count / total_pokemon) * 100 for count in chart_data['values']]
    }

    # Calculate additional statistics
    avg_stats = db.session.query(
        db.func.avg(Pokemon.hp).label('hp'),
        db.func.avg(Pokemon.attack).label('attack'),
        db.func.avg(Pokemon.defense).label('defense'),
        db.func.avg(Pokemon.sp_attack).label('sp_attack'),
        db.func.avg(Pokemon.sp_defense).label('sp_defense'),
        db.func.avg(Pokemon.speed).label('speed')
    ).first()

    # Add this new section
    combined_type_data = get_combined_type_distribution()
    combined_chart_data = {
        'labels': list(combined_type_data.keys()),
        'values': list(combined_type_data.values()),
        'colors': [type_colors.get(type_, '#FFFFFF') for type_ in combined_type_data.keys()]
    }

    return render_template("pokemon_dashboard.html", 
                           chart_data=chart_data, 
                           pie_chart_data=pie_chart_data, 
                           combined_chart_data=combined_chart_data,
                           total_pokemon=total_pokemon, 
                           avg_stats=avg_stats._asdict())

@app.route("/pokemon-piechart", methods=['GET'])
@jwt_required()
def pokemon_piechart():
  # Retrieve Pokémon data for charting
  pokemon_data = db.session.query(
      Pokemon.type1,
      db.func.count(Pokemon.id).label('count')
  ).group_by(Pokemon.type1).all()
  
  # Prepare data for Chart.js
  chart_data = {
      'labels': [data.type1 for data in pokemon_data],
      'values': [data.count for data in pokemon_data]
  }

  return render_template("pokemon_piechart.html", chart_data=chart_data)


@app.route("/release-pokemon/<int:pokemon_id>", methods=['POST'])
@jwt_required()
def release_action(pokemon_id):
  # Find the user's Pokémon to release
  user_pokemon = UserPokemon.query.filter_by(user_id=current_user.get_json()['id'], pokemon_id=pokemon_id).first()

  if user_pokemon:
    # Delete the user's Pokémon
    db.session.delete(user_pokemon)
    db.session.commit()
    flash('Successfully released the Pokémon!')
  else:
    flash('Error: Pokémon not found.')

  return redirect(request.referrer)

if __name__ == "__main__":
  app.run(host='0.0.0.0', port=8080)
