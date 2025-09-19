from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Station(db.Model):
    __tablename__ = "stations"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(100))

    platforms = db.relationship("Platform", backref="station", lazy=True)

class Platform(db.Model):
    __tablename__ = "platforms"
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, nullable=False)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)

class Train(db.Model):
    __tablename__ = "trains"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    train_type = db.Column(db.String(50))  # local, express, freight
    priority = db.Column(db.Integer, default=1)  # higher = more important

    route_id = db.Column(db.Integer, db.ForeignKey("routes.id"))

class Route(db.Model):
    __tablename__ = "routes"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))

    trains = db.relationship("Train", backref="route", lazy=True)
