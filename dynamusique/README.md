# Dynamusique

La musique de Dylan et Léna : des sons téléchargés depuis YouTube, des profils, des playlists, l'aléatoire, et l'écoute écran verrouillé.

## Installer

1. Sur le PC, télécharger **[Dynamusique.exe](https://github.com/dylker24/frigo-rose-tv/releases/download/dynamusique/Dynamusique.exe)** et le lancer (Windows peut demander « Exécuter quand même »).
2. La page s'ouvre sur le PC et affiche l'adresse pour les téléphones (`http://100.x.x.x:8797`).
3. Sur chaque iPhone (Tailscale activé) : ouvrir cette adresse dans **Safari** → **Partager** → **Sur l'écran d'accueil**.

Dynamusique se lance tout seul avec Windows. Pour mettre à jour : télécharger le nouveau `.exe` et le lancer, il remplace l'ancien tout seul.

**Hors-ligne** : l'appli s'ouvre via l'adresse https de Tailscale (`https://nom-du-pc….ts.net:8798`). Dans une playlist, activer « Sur ce téléphone » pour garder ses sons sur le tél. Les sons sont rangés dans `Musique\Dynamusique`, la bibliothèque dans `%LOCALAPPDATA%\Dynamusique`.
Si Frigo Rosé Studio est installé, Dynamusique réutilise ses outils (yt-dlp, ffmpeg).

## Fichiers

- `dynamusique.py` : le serveur qui tourne sur le PC (téléchargements, profils, playlists, envoi de l'audio)
- `page.html` : l'appli téléphone
- Le `.exe` est fabriqué automatiquement par GitHub (`.github/workflows/dynamusique.yml`) à chaque modification.
