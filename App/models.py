from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
db = SQLAlchemy()


class UserPokemon(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
  pokemon_id = db.Column(db.Integer, db.ForeignKey('pokemon.id'))
  pokemon = db.relationship('Pokemon')
  name = db.Column(db.String(50))

  def __init__(self, user_id, pokemon_id, name):
    self.user_id = user_id
    self.pokemon_id = pokemon_id
    self.name = name
  
  def __repr__(self):
      return f'<UserPokemon {self.id} : {self.name} trainer {self.user.username}>'
  
  def get_json(self):
    return{
      'id': self.id,
      'name': self.name,
      'species': self.pokemon.name
    }

class User(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  username = db.Column(db.String(80), unique=True, nullable=False)
  email = db.Column(db.String(120), unique=True, nullable=False)
  password = db.Column(db.String(120), nullable=False)
  pokemon = db.relationship('UserPokemon', backref='user')

  def __init__(self, username, email, password):
    self.username = username
    self.email = email
    self.set_password(password)
  

  def catch_pokemon(self, pokemon_id, name):
    poke = Pokemon.query.get(pokemon_id)
    if poke:
      try:
        pokemon = UserPokemon(self.id, pokemon_id, name)
        db.session.add(pokemon)
        db.session.commit()
        return pokemon
      except Exception as e:
        print(e)
        db.session.rollback()
        return None
    return None

  def release_pokemon(self, poke_id):
    poke = UserPokemon.query.get(poke_id)
    if poke.user == self:
      db.session.delete(poke)
      db.session.commit()
      return True
    return None

  def rename_pokemon(self, poke_id, name):
    poke = UserPokemon.query.get(poke_id)
    if poke.user == self:
      poke.name = name
      db.session.add(poke)
      db.session.commit()
      return True
    return None
    
  #hashes the password parameter and stores it in the object
  def set_password(self, password):
      """Create hashed password."""
      self.password = generate_password_hash(password, method='sha256')
  
  #Returns true if the parameter is equal to the object’s password property
  def check_password(self, password):
      """Check hashed password."""
      return check_password_hash(self.password, password)
  
  #To String method
  def __repr__(self):
      return f'<User {self.id}: {self.username}>'


  def get_json(self):
    return {
      'id': self.id,
      'username': self.username,
      'email': self.email
    }

class Pokemon(db.Model):
  id = db.Column(db.Integer, primary_key=True)
  name = db.Column(db.String(255), nullable=False)
  pokedex_number = db.Column(db.Integer, nullable=False)
  attack = db.Column(db.Integer, nullable=False)
  defense = db.Column(db.Integer, nullable=False)
  hp = db.Column(db.Integer, nullable=False)
  height = db.Column(db.Float) 
  sp_attack = db.Column(db.Integer, nullable=False)
  sp_defense = db.Column(db.Integer, nullable=False)
  speed = db.Column(db.Integer, nullable=False)
  type1 = db.Column(db.String(255), nullable=False)
  type2 = db.Column(db.String(255), default=None)
  weight = db.Column(db.Float)
  generation = db.Column(db.Integer, nullable=False)
  classification = db.Column(db.String(255), nullable=False)
  abilities = db.Column(db.String(255))

  def get_json(self):
   return {
     'pokemon_id': self.id,
     'name': self.name,
     'pokedex_number': self.pokedex_number,
     'attack': self.attack,
     'defense': self.defense,
     'hp': self.hp,
     'height': self.height,
     'sp_attack': self.sp_attack,
     'sp_defense': self.sp_defense,
     'speed': self.speed,
     'type1': self.type1,
     'type2': self.type2,
     'weight': self.weight,
     'generation': self.generation,
     'classification': self.classification,
     'abilities': self.abilities.split(',') if self.abilities else [],
   }

