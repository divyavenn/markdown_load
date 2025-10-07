from youtube import download_audio
from pathlib import Path

output = Path("audio_files")

videos = [
  "https://youtu.be/joE_rxa3MjQ?si=fbJcZGK_IbJ077cG",
  "https://youtu.be/KIE_D-Wtxvg?si=6cZifMeykCrPJvS9",
  "https://youtu.be/Cug8G2HxPDY?si=hbDF-KPsg9CLv6cu",
  "https://youtu.be/371_2fWzekE?si=W-eJHsbbGTgpJQ7-",
  "https://youtu.be/uJtQueg9mNE?si=yI761UlD1taamep_",
]

for url in videos:
  download_audio(url, out_dir=output, video_id=url.split("/")[-1], cookies=None)