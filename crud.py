from sqlalchemy.orm import Session
from fastapi import HTTPException
from models import Video, Progress
from youtube_api import get_youtube_video_data
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from sqlalchemy.orm import Session
from fastapi import HTTPException
from models import Video, Review
from schemas import ReviewCreate


def extract_video_id(youtube_url: str) -> str:
    """
    Extrait l'ID de la vidéo depuis une URL YouTube.
    Fonctionne pour les formats suivants :
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    """
    parsed_url = urlparse(youtube_url)
    if "youtu.be" in parsed_url.netloc:
        return parsed_url.path.lstrip("/")
    if "youtube.com" in parsed_url.netloc:
        return parse_qs(parsed_url.query).get("v", [None])[0]
    return None


def create_video(db: Session, youtube_input: str, mentor_email: str, category: str, order: int = None):
    """
    Crée une vidéo dans la BDD en extrayant les infos depuis YouTube.
    L'ordre d'affichage est calculé en fonction du mentor et de la catégorie.

    Étapes :
    1. Vérifie si la vidéo est déjà enregistrée (évite les doublons).
    2. Détermine si l'entrée est une URL ou un ID.
    3. Récupère les données de la vidéo via l'API YouTube.
    4. Calcule dynamiquement l'ordre si non fourni.
    5. Crée et enregistre l'objet Video dans la base de données.

    Raises:
        HTTPException: En cas d'erreur d'extraction de l'ID, de récupération des données ou si la vidéo existe déjà.
    """
    # Vérification si la vidéo existe déjà dans la BDD
    existing_video = db.query(Video).filter(Video.youtube_url == youtube_input).first()
    if existing_video:
        raise HTTPException(status_code=400, detail="Cette vidéo est déjà enregistrée")

    # Détermine si on a une URL ou directement un ID
    if "youtu" in youtube_input:
        video_id = extract_video_id(youtube_input)
        if not video_id:
            raise HTTPException(status_code=400, detail="Impossible d'extraire l'ID de la vidéo depuis l'URL fournie")
    else:
        video_id = youtube_input  # On considère que c'est déjà un ID

    # Récupérer les données de la vidéo via l'API YouTube
    video_data = get_youtube_video_data(video_id)
    if video_data is None:
        raise HTTPException(status_code=400, detail="Impossible de récupérer les données de la vidéo depuis YouTube")

    # Calculer l'ordre si non fourni en fonction du dernier enregistrement pour le couple (mentor_email, category)
    if order is None:
        last_video = (
            db.query(Video)
            .filter(Video.mentor_email == mentor_email, Video.category == category)
            .order_by(Video.order.desc())
            .first()
        )
        order = last_video.order + 1 if last_video and last_video.order is not None else 1

    # Création de l'instance Video avec les données récupérées
    video = Video(
        youtube_url=video_data["video_id"],  # On stocke uniquement l'ID de la vidéo
        mentor_email=mentor_email,
        category=category,
        title=video_data["title"],
        description=video_data["description"],
        publication_date=datetime.fromisoformat(video_data["publication_date"].replace("Z", "+00:00")),
        views=video_data["views"],
        likes=video_data["likes"],
        order=order
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def track_progress(db: Session, video_id: int, mentee_email: str):
    """
    Met à jour ou crée une nouvelle progression de visionnage pour une vidéo donnée.

    Étapes :
    1. Vérifie que la vidéo existe dans la BDD.
    2. Cherche une progression existante pour le couple (video_id, mentee_email).
    3. Incrémente la progression si elle existe, sinon crée un nouvel enregistrement.

    Raises:
        HTTPException: Si la vidéo n'est pas trouvée.
    """
    # Vérifier l'existence de la vidéo
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Vidéo non trouvée")

    # Rechercher une progression existante pour ce mentee
    progress = db.query(Progress).filter(
        Progress.video_id == video_id,
        Progress.mentee_email == mentee_email
    ).first()
    if progress:
        progress.watched += 1
    else:
        progress = Progress(video_id=video_id, mentee_email=mentee_email, watched=1)
        db.add(progress)
    db.commit()
    return progress


def update_video(db: Session, video_id: int, title: str = None, description: str = None, category: str = None):
    """
    Met à jour les informations d'une vidéo existante.

    Args:
        title (str, optionnel): Nouveau titre de la vidéo.
        description (str, optionnel): Nouvelle description.
        category (str, optionnel): Nouvelle catégorie.

    Raises:
        HTTPException: Si la vidéo n'est pas trouvée.
    """
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Vidéo non trouvée")

    if title:
        video.title = title
    if description:
        video.description = description
    if category:
        video.category = category

    db.commit()
    db.refresh(video)
    return video


def delete_video(db: Session, video_id: int):
    """
    Supprime une vidéo existante de la base de données.

    Raises:
        HTTPException: Si la vidéo n'est pas trouvée.
    """
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Vidéo non trouvée")

    db.delete(video)
    db.commit()
    return video


def add_review(db: Session, review_data: ReviewCreate):
    """
    Ajoute un avis à une vidéo.
    """
    try:
        # Vérifier si la vidéo existe
        video = db.query(Video).filter(Video.id == review_data.video_id).first()
        if not video:
            raise HTTPException(status_code=404, detail="Vidéo non trouvée")

        # Vérifier si l'utilisateur a déjà laissé un avis pour éviter les doublons
        existing_review = db.query(Review).filter(
            Review.video_id == review_data.video_id,
            Review.mentee_email == review_data.mentee_email
        ).first()
        if existing_review:
            raise HTTPException(status_code=400, detail="Vous avez déjà laissé un avis pour cette vidéo")

        # Créer la review
        review = Review(
            video_id=review_data.video_id,
            mentee_email=review_data.mentee_email,
            stars=review_data.stars,
            comment=review_data.comment
        )
        db.add(review)
        db.commit()
        db.refresh(review)
        return review

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur interne lors de l'ajout de l'avis: {e}")


def get_reviews_for_video(db: Session, video_id: int):
    """
    Récupère les avis d'une vidéo.
    """
    try:
        reviews = db.query(Review).filter(Review.video_id == video_id).all()
        return reviews
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur interne lors de la récupération des avis: {e}")


def get_average_rating(db: Session, video_id: int):
    """
    Calcule la note moyenne d'une vidéo.
    """
    try:
        reviews = db.query(Review).filter(Review.video_id == video_id).all()
        if not reviews:
            return 0
        total_stars = sum(review.stars for review in reviews)
        return round(total_stars / len(reviews), 2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur interne lors du calcul de la note moyenne: {e}")