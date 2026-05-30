from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class UserPokemon(db.Model):
    __tablename__ = 'user_pokemon'
    __table_args__ = (
        db.Index('ix_user_pokemon_user_id', 'user_id'),
        db.Index('ix_user_pokemon_pokemon_id', 'pokemon_id'),
    )

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
        return {
            'id': self.id,
            'pokemon_id': self.pokemon_id,
            'name': self.name,
            'species': self.pokemon.name
        }


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    pokemon = db.relationship('UserPokemon', backref='user')
    messages = db.relationship('Message', backref='sender', lazy=True)

    def __init__(self, username, email, password):
        self.username = username
        self.email = email
        self.set_password(password)
    
    def catch_pokemon(self, pokemon_id, name):
        poke = db.session.get(Pokemon, pokemon_id)
        if poke:
            try:
                user_poke = UserPokemon(self.id, pokemon_id, name)
                db.session.add(user_poke)
                db.session.commit()
                return user_poke
            except Exception:
                db.session.rollback()
                return None
        return None

    def release_pokemon(self, poke_id):
        poke = db.session.get(UserPokemon, poke_id)
        if poke and poke.user_id == self.id:
            db.session.delete(poke)
            db.session.commit()
            return True
        return None

    def rename_pokemon(self, poke_id, name):
        poke = db.session.get(UserPokemon, poke_id)
        if poke and poke.user_id == self.id:
            poke.name = name
            db.session.add(poke)
            db.session.commit()
            return True
        return None
    
    def set_password(self, password):
        """Create hashed password."""
        self.password = generate_password_hash(password, method='sha256')
    
    def check_password(self, password):
        """Check hashed password."""
        return check_password_hash(self.password, password)
    
    def __repr__(self):
        return f'<User {self.id}: {self.username}>'

    def get_json(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email
        }


class Pokemon(db.Model):
    __tablename__ = 'pokemon'
    __table_args__ = (
        db.Index('ix_pokemon_name', 'name'),
        db.Index('ix_pokemon_type1', 'type1'),
        db.Index('ix_pokemon_type2', 'type2'),
        db.Index('ix_pokemon_generation', 'generation'),
        db.Index('ix_pokemon_is_legendary', 'is_legendary'),
        db.Index('ix_pokemon_type1_generation', 'type1', 'generation'),
        db.Index('ix_pokemon_type1_type2', 'type1', 'type2'),
    )

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
    capture_rate = db.Column(db.Integer, default=45)
    is_legendary = db.Column(db.Integer, default=0)
    percentage_male = db.Column(db.Float, default=50.0)
    base_total = db.Column(db.Integer)
    base_egg_steps = db.Column(db.Integer, default=0)
    base_happiness = db.Column(db.Integer, default=0)
    experience_growth = db.Column(db.Integer, default=0)

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
            'capture_rate': self.capture_rate,
            'is_legendary': self.is_legendary,
            'percentage_male': self.percentage_male,
            'base_total': self.base_total,
            'base_egg_steps': self.base_egg_steps,
            'base_happiness': self.base_happiness,
            'experience_growth': self.experience_growth
        }


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    room = db.Column(db.String(100), nullable=False)
    text = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)

    def __repr__(self):
        return f'<Message {self.id} from {self.sender_id} in {self.room}>'

    def to_json(self):
        return {
            'id': self.id,
            'sender_id': self.sender_id,
            'username': self.sender.username if self.sender else None,
            'room': self.room,
            'text': self.text,
            'timestamp': self.timestamp.isoformat()
        }
