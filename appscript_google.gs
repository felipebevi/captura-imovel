function doPost(e) {
  try {
    var sheet = SpreadsheetApp.openById("xxx").getSheetByName("Planilha1");
    var data = JSON.parse(e.postData.contents);

    var gps = data.gps || {};
    var endereco = data.endereco || "";
    var status = (data.status || []).join(", ");
    var numero = data.numero_casa || "";
    var telefones = (data.telefones || []).join(", ");

    var agora = new Date();
    sheet.appendRow([
      Utilities.formatDate(agora, "GMT-3", "yyyy-MM-dd HH:mm:ss"),
      endereco,
      gps.lat || "",
      gps.lon || "",
      status,
      numero,
      telefones,
      data.foto_url || ''
    ]);

    return ContentService.createTextOutput(JSON.stringify({ sucesso: true })).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ erro: err.message })).setMimeType(ContentService.MimeType.JSON);
  }
}
