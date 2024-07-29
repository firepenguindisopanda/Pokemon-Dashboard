import os, csv
import datetime
from flask import Flask, request, redirect, render_template, url_for, flash
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
from .models import db, User, UserPokemon, Pokemon

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

# JWT Config to enable current_user
@jwt.user_identity_loader
def user_identity_lookup(user):
  return user.id

@jwt.user_lookup_loader
def user_lookup_callback(_jwt_header, jwt_data):
  identity = jwt_data["sub"]
  return User.query.get(identity)

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
  # Query the database for Pokémon matching the search query
  matching_pokemon = Pokemon.query.filter(Pokemon.name.ilike(f'%{query}%')).all()
  # Convert the matching Pokémon to JSON format
  results = [pokemon.get_json() for pokemon in matching_pokemon]
  return results

def filter_pokemon_by_generation(generation):
  # Query the database for Pokémon belonging to the specified generation
  matching_pokemon = Pokemon.query.filter_by(generation=generation).all()
  # Convert the matching Pokémon to JSON format
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
  pokemon = Pokemon.query.get(pokemon_id).get_json()
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
  pokemon_to_display_details = Pokemon.query.get(pokemon_id).get_json()
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
  
  # Find the user's Pokémon to rename
  user_pokemon = UserPokemon.query.get(pokemon_id)
  
  print('Specific Pokemon: ', user_pokemon.id)
  print('New Name: ', new_name)
  if current_user.rename_pokemon(user_pokemon.id, new_name):
    message = "Your Pokemon " + user_pokemon.get_json()['species'] + " has been given a new successfully!"
    flash(message)
  else:
    flash("We encountered an error while renaming your pokemon. Please make sure you correctly provided a name")

  return redirect(request.referrer)

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
