from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    send_from_directory,
    session,
    abort
)
from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    SubmitField,
    ValidationError,
)
from wtforms.validators import DataRequired, Length, ValidationError
from flask_sqlalchemy import SQLAlchemy
import postgresqlite
import sqlalchemy as sa
from flask_wtf.file import FileField
import os
from datetime import datetime
import uuid
import smtplib
from email.message import EmailMessage
from flask_wtf.csrf import CSRFProtect

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SECRET_KEY"] = "a_very_secret_key"
app.config["SQLALCHEMY_DATABASE_URI"] = postgresqlite.get_uri()
db = SQLAlchemy(app)
csrf = CSRFProtect(app)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


class Loginform(FlaskForm):
    username = StringField(
        "Username", validators=[DataRequired(), Length(min=2, max=20)]
    )
    submit = SubmitField("Email login link")

    def validate_username(self, field):
        if not field.data.isalpha():
            raise ValidationError("Username may only exist of letters")


class Mealform(FlaskForm):
    description = StringField("Description", validators=[Length(min=4, max=40)])
    expenses = StringField("Total expenses")
    photo = FileField("Upload groceries receipt")
    submit = SubmitField("Submit")

    def validate_expenses(self, field):
        if not field.data:
            return

        try:
            value = float(field.data.replace(",", "."))
        except ValueError:
            raise ValidationError("Enter a valid amount (e.g. 15,23)")

        if value < 1 or value > 100:
            raise ValidationError("Amount must be between 1 and 100 EUR")


class ParticipateForm(FlaskForm):
    submit = SubmitField("Participate")


class UnparticipateForm(FlaskForm):
    submit = SubmitField("Unparticipate")


class CookForm(FlaskForm):
    submit = SubmitField("Cook this day")


UserMeal = db.Table(
    "user_meal",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id")),
    db.Column("meal_id", db.Integer, db.ForeignKey("meal.id")),
)


class User(db.Model):
    id = sa.Column(sa.Integer, primary_key=True)
    username = sa.Column(sa.String, nullable=False)
    meals = db.relationship("Meal", secondary=UserMeal, back_populates="participants")

    def get_cooking_balance(self):
        balance = 0
        for meal in Meal.query.all():
            if meal.cook == self:
                balance += len(meal.participants) or 0
            if self in meal.participants:
                balance -= 1
        return balance

    def get_expenses_balance(self):
        balance = 0
        for meal in Meal.query.all():
            if meal.cook == self and meal.expenses:
                balance += float(meal.expenses)
            if (
                self in meal.participants
                and meal.expenses
                and len(meal.participants) > 0
            ):
                balance -= float(meal.expenses) / len(meal.participants)
        return round(balance, 2)


class Meal(db.Model):
    id = sa.Column(sa.Integer, primary_key=True)
    date = sa.Column(sa.Date, nullable=False)
    cook_id = sa.Column(sa.Integer, sa.ForeignKey("user.id"))
    cook = db.relationship("User")
    description = sa.Column(sa.String, nullable=True)
    number_of_participants = sa.Column(sa.Integer, nullable=True)
    expenses = sa.Column(sa.Numeric(10, 2), nullable=True)
    participants = db.relationship("User", secondary=UserMeal, back_populates="meals")
    photo_filename = sa.Column(sa.String, nullable=True)


class LoginToken(db.Model):
    id = sa.Column(sa.Integer, primary_key=True)
    token = sa.Column(sa.String, unique=True, nullable=False)
    username = sa.Column(sa.String, nullable=False)
    used = sa.Column(sa.Boolean, default=False)


def send_email(to_email, link):
    msg = EmailMessage()
    msg.set_content(f"Klik hier om in te loggen: {link}")
    msg["Subject"] = "Login link"
    msg["From"] = "noreply@huizebrak.nl"
    msg["To"] = to_email

    with smtplib.SMTP("smtp.ethereal.email", 587) as smtp:
        smtp.starttls()
        smtp.login("kenna.douglas@ethereal.email", "ky3jJh3Nz3nb5H5C7w")
        smtp.send_message(msg)


@app.route("/login/<token>")
def login_with_token(token):
    login_token = (
        db.session.execute(db.select(LoginToken).filter(LoginToken.token == token))
        .scalars()
        .first()
    )

    if not login_token or login_token.used:
        return "No access"

    user = (
        db.session.execute(
            db.select(User).filter(User.username == login_token.username)
        )
        .scalars()
        .first()
    )

    if not user:
        user = User(username=login_token.username)
        db.session.add(user)
        db.session.commit()

    session["user"] = user.id

    login_token.used = True
    db.session.commit()

    return redirect(url_for("index"))


@app.route("/", methods=["GET", "POST"])
def index():
    form = Loginform()
    mailmessage = ""
    if form.validate_on_submit():
        username = form.username.data

        token = uuid.uuid4().hex

        login_token = LoginToken(token=token, username=username)
        db.session.add(login_token)
        db.session.commit()

        link = url_for("login_with_token", token=token, _external=True)

        send_email(f"{username}@huizebrak.nl", link)

        mailmessage = "Check your email for a login link"

    if "user" not in session:
        return render_template("login.html", form=form, mailmessage=mailmessage)

    today = datetime.today().date()
    meal_today = (
        db.session.execute(db.select(Meal).filter(Meal.date == today)).scalars().first()
    )
    if not meal_today:
        meal = Meal(date=today)
        db.session.add(meal)
        db.session.commit()

    users = User.query.all()

    meals = Meal.query.order_by(Meal.date.desc()).all()

    return render_template("index.html", meals=meals, users=users)


@app.route("/participate/<int:meal_id>", methods=["POST"]) 
def participate(meal_id):
    form = ParticipateForm()
    if not form.validate_on_submit():
        abort(404)
    meal = (
        db.session.execute(db.select(Meal).filter(Meal.id == meal_id)).scalars().first()
    )
    
    if meal is None:
        abort(404)
    
    if meal.photo_filename is not None:
        abort(404)

    if "user" not in session:
        return redirect(url_for("index"))
    user_id = session["user"]
    current_user = db.session.get(User, user_id)

    if current_user not in meal.participants:
        meal.participants.append(current_user)
        db.session.commit()
    return redirect(url_for("meal", meal_id=meal.id, current_user=current_user))


@app.route("/unparticipate/<int:meal_id>", methods=["POST"])
def unparticipate(meal_id):
    form = UnparticipateForm()
    if not form.validate_on_submit():
        abort(404)

    if "user" not in session:
        return redirect(url_for("index"))

    user_id = session["user"]
    current_user = db.session.get(User, user_id)

    if current_user is None:
        return redirect(url_for("index"))

    meal = db.session.get(Meal, meal_id)
    if meal is None:
        return redirect(url_for("index"))

    if current_user in meal.participants:
        meal.participants.remove(current_user)
        db.session.commit()

    return redirect(url_for("meal", meal_id=meal.id, current_user=current_user))


@app.route("/cook/<int:meal_id>", methods=["POST"])
def cook(meal_id):
    form = CookForm()
    if not form.validate_on_submit():
        abort(404)

    meal = db.session.get(Meal, meal_id)
    if meal is None:
        abort(404)

    if "user" not in session:
        return redirect(url_for("index"))
    user_id = session["user"]

    current_user = db.session.get(User, user_id)

    if meal.cook and meal.cook != current_user:
        abort(403)

    meal.cook = current_user

    db.session.commit()
    return redirect(url_for("meal", meal_id=meal.id))


@app.route("/meal/<int:meal_id>", methods=["GET", "POST"])
def meal(meal_id):
    form = Mealform()
    cook_form = CookForm()
    participate_form = ParticipateForm()
    unparticipate_form = UnparticipateForm()

    meal = db.session.get(Meal, meal_id)
    if meal is None:
        abort(404)

    form = Mealform(obj=meal)

    if form.validate_on_submit():
        meal.description = form.description.data
        meal.expenses = form.expenses.data
        if form.photo.data:
            filename = f"{uuid.uuid4().hex}.png"
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            form.photo.data.save(file_path)
            meal.photo_filename = filename
        db.session.commit()
        return redirect(url_for("meal", meal_id=meal.id))
    if "user" not in session:
        return redirect(url_for("index"))
    user_id = session["user"]
    current_user = db.session.get(User, user_id)
    return render_template(
        "meal.html",
        form=form,
        meal=meal,
        current_user=current_user,
        cook_form=cook_form,
        participate_form=participate_form,
        unparticipate_form=unparticipate_form,
    )


@app.route("/logout", methods=["GET"])
def logout():
    session.pop("user", None)
    return redirect(url_for("index"))


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    if "user" not in session:
        return redirect(url_for("index"))
    return send_from_directory(UPLOAD_FOLDER, filename)


with app.app_context():
    db.drop_all()
    db.create_all()
