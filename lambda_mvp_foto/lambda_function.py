
import boto3
import exifread
import tempfile
import requests
import re
import json
import base64
import uuid
import os
import email
from PIL import Image
import pyheif
from datetime import datetime, timezone


s3 = boto3.client('s3')
rekognition = boto3.client('rekognition')

BUCKET_NAME = "imoveis-fotos-thomaz"
GOOGLE_API_KEY = "xxx"

def extrai_gps(image_path):
    print(f"[DEBUG] Extraindo GPS da imagem: {image_path}")
    try:
        with open(image_path, 'rb') as f:
            tags = exifread.process_file(f)
        print(f"[DEBUG] Tags EXIF encontradas: {list(tags.keys())}")

        lat_ref = tags["GPS GPSLatitudeRef"].values
        lat = tags["GPS GPSLatitude"].values
        lon_ref = tags["GPS GPSLongitudeRef"].values
        lon = tags["GPS GPSLongitude"].values

        def convert_to_degrees(value):
            d, m, s = [float(x.num) / float(x.den) for x in value]
            return d + (m / 60.0) + (s / 3600.0)

        lat_val = convert_to_degrees(lat)
        lon_val = convert_to_degrees(lon)
        if lat_ref != "N": lat_val *= -1
        if lon_ref != "E": lon_val *= -1

        print(f"[DEBUG] Coordenadas extraídas: lat={lat_val}, lon={lon_val}")
        return lat_val, lon_val
    except Exception as e:
        print(f"[ERROR] Falha ao extrair GPS: {e}")
        return None, None

def extrai_telefones(textos):
    padrao = re.compile(r'(\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4})|(\d{4,5}[-\s]?\d{4})')
    encontrados = set()
    for t in textos:
        encontrados.update(padrao.findall(t))
    limpos = {match[0] or match[1] for match in encontrados if match[0] or match[1]}
    print(f"[DEBUG] Telefones encontrados: {limpos}")
    return list(limpos)

def converter_heic_para_jpeg(heic_path, jpeg_path):
    heif_file = pyheif.read(heic_path)
    imagem = Image.frombytes(heif_file.mode, heif_file.size, heif_file.data, "raw", heif_file.mode)
    imagem.save(jpeg_path, format="JPEG")
    print(f"[DEBUG] HEIC convertido para JPEG: {jpeg_path}")

def parse_multipart_lambda(event):
    content_type = event['headers'].get('content-type') or event['headers'].get('Content-Type')
    body = base64.b64decode(event['body']) if event.get('isBase64Encoded') else event['body'].encode()
    msg = email.message_from_bytes(f"Content-Type: {content_type}\n\n".encode() + body)

    for part in msg.walk():
        if part.get_content_disposition() == 'form-data':
            filename = part.get_filename()
            payload = part.get_payload(decode=True)
            return filename, payload
    return None, None

def lambda_handler(event, context):
    try:
        print("[INFO] Lambda iniciada")
        print(f"[DEBUG] Evento recebido: {str(event)[:1000]}")

        content_type = event.get("headers", {}).get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            print("[DEBUG] multipart/form-data recebido")
            filename, filedata = parse_multipart_lambda(event)
            if not filename or not filedata:
                return {"statusCode": 400, "body": json.dumps({"erro": "Arquivo ausente"})}
            local_path = f"/tmp/{filename}"
            with open(local_path, "wb") as f:
                f.write(filedata)
        else:
            body = event.get("body")
            if event.get("isBase64Encoded"):
                print("[DEBUG] Corpo está codificado em base64, decodificando...")
                body = base64.b64decode(body).decode()
            dados = json.loads(body) if isinstance(body, str) else body
            filename = dados.get("filename") or f"upload-{uuid.uuid4()}.jpg"
            image_base64 = dados.get("image_base64")
            if not image_base64:
                return {"statusCode": 400, "body": json.dumps({"erro": "Campo 'image_base64' ausente"})}
            filedata = base64.b64decode(image_base64)
            local_path = f"/tmp/{filename}"
            with open(local_path, "wb") as f:
                f.write(filedata)

        ext = os.path.splitext(filename)[1].lower()
        if ext == ".heic":
            converted_path = local_path.replace(".heic", ".jpg")
            converter_heic_para_jpeg(local_path, converted_path)
            filename = os.path.basename(converted_path)
            local_path = converted_path

        print(f"[DEBUG] Arquivo salvo em: {local_path}")

        hoje = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        s3_key = f"{hoje}/{filename}"

        s3.upload_file(
            local_path,
            BUCKET_NAME,
            s3_key,
            ExtraArgs={
                'ContentType': 'image/jpeg'
            }
        )
        print(f"[INFO] Arquivo enviado para o S3: {BUCKET_NAME}/{s3_key}")

        lat, lon = extrai_gps(local_path)
        # Fallback: se não extraiu do EXIF, tenta pegar do corpo do request
        if not lat or not lon:
            try:
                qs = event.get("queryStringParameters") or {}
                lat = float(qs.get("lat")) if qs.get("lat") else None
                lon = float(qs.get("lon")) if qs.get("lon") else None
                if lat and lon:
                    print(f"[DEBUG] GPS manual recebido por querystring: lat={lat}, lon={lon}")
            except Exception as e:
                print(f"[DEBUG] Erro ao extrair lat/lon da querystring: {e}")


        rekog_response = rekognition.detect_text(Image={'S3Object': {'Bucket': BUCKET_NAME, 'Name': s3_key}})
        textos = [d['DetectedText'].lower() for d in rekog_response['TextDetections']]
        print(f"[DEBUG] Textos detectados pelo Rekognition: {textos}")

        status = []
        if any("vende" in t for t in textos): status.append("vende")
        if any("aluga" in t for t in textos): status.append("aluga")
        print(f"[DEBUG] Status detectado: {status}")

        numero = next((int(t) for t in textos if t.isdigit() and 1 <= len(t) <= 3), None)
        print(f"[DEBUG] Número da casa detectado: {numero}")

        telefones = extrai_telefones(textos)

        endereco = None
        if lat and lon:
            try:
                url = f"https://maps.googleapis.com/maps/api/geocode/json?latlng={lat},{lon}&key={GOOGLE_API_KEY}"
                r = requests.get(url)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("results"):
                        endereco = data["results"][0]["formatted_address"]
                print(f"[DEBUG] Endereço detectado: {endereco}")
            except Exception as e:
                print(f"[ERROR] Falha ao buscar endereço: {e}")
                
        image_url = f"https://{BUCKET_NAME}.s3.us-east-2.amazonaws.com/{s3_key}"
        print(f"[DEBUG] URL da imagem: {image_url}")

        response = {
            "gps": {"lat": lat, "lon": lon} if lat and lon else None,
            "endereco": endereco,
            "status": status,
            "numero_casa": numero,
            "telefones": telefones,
            "foto_url": image_url
        }
        try:
            requests.post(
                "https://script.google.com/macros/s/xxxx/exec",
                json=response,
                timeout=5
            )
            print("[INFO] Dados enviados à planilha com sucesso!")
        except Exception as e:
            print(f"[ERROR] Falha ao enviar dados para planilha: {e}")


        print(f"[INFO] Resposta final: {response}")
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(response)
        }

    except Exception as e:
        import traceback
        print(f"[FATAL] Erro inesperado: {str(e)}")
        traceback.print_exc()
        return {
            "statusCode": 500,
            "body": json.dumps({"erro": str(e)})
        }
