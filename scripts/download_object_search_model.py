from __future__ import annotations

import argparse
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

from mobile_ingestion.config import object_search_local_model_dir
from mobile_ingestion.config import is_complete_object_search_local_model_dir


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
      description=(
          "Télécharge un snapshot local du modèle de recherche d'objet dans "
          "le dossier models/ du repo."
      ))
  parser.add_argument(
      "--model-id",
      default="google/owlv2-large-patch14-ensemble",
      help="Identifiant Hugging Face du modèle à télécharger.",
  )
  parser.add_argument(
      "--target-dir",
      default=None,
      help=(
          "Dossier cible. Par défaut: models/<model-id-sanitized> dans le repo."
      ),
  )
  parser.add_argument(
      "--force",
      action="store_true",
      help="Force une reprise/téléchargement même si le dossier existe déjà.",
  )
  return parser.parse_args()


def resolve_target_dir(model_id: str, target_dir: str | None) -> Path:
  if target_dir:
    return Path(target_dir).expanduser().resolve()
  return object_search_local_model_dir(model_id).resolve()


def main() -> int:
  args = parse_args()
  target_dir = resolve_target_dir(args.model_id, args.target_dir)
  target_dir.parent.mkdir(parents=True, exist_ok=True)

  if is_complete_object_search_local_model_dir(target_dir) and not args.force:
    print(f"Le modèle local existe déjà: {target_dir}")
    print("Utilise --force pour relancer le téléchargement.")
    return 0

  print(f"Téléchargement du modèle {args.model_id}")
  print(f"Destination locale: {target_dir}")
  snapshot_path = snapshot_download(
      repo_id=args.model_id,
      local_dir=str(target_dir),
      force_download=args.force,
  )
  marker_path = target_dir / ".download-complete"
  marker_path.write_text(
      f"repo_id={args.model_id}\nsnapshot_path={snapshot_path}\n",
      encoding="utf-8",
  )
  print("Téléchargement terminé.")
  print(f"Snapshot local prêt: {snapshot_path}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
