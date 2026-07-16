import QtQuick
import QtQuick.Window
import QtQuick.Layouts

Window {
    visible: true
    width: 800          // a common embedded touchscreen size
    height: 480
    title: "Edge Device"
    color: "#0f1419"

    property real temp: 0
    property real pressure: 0
    property real humidity: 0
    property string status: "starting"
    property string reasons: ""

    function statusColor(s) {
        if (s === "normal")   return "#3fb950";
        if (s === "warning")  return "#d29922";
        if (s === "critical") return "#f85149";
        if (s === "invalid")  return "#6e7681";
        return "#58a6ff";
    }

    // Listen for the signal emitted by the worker thread.
    Connections {
        target: device
        function onReading(t, p, h, s, why) {
            temp = t; pressure = p; humidity = h; status = s; reasons = why;
        }
    }

    component Tile: Rectangle {
        property string label: ""
        property real value: 0
        property string unit: ""
        radius: 16
        color: "#1c2128"
        ColumnLayout {
            anchors.centerIn: parent
            spacing: 4
            Text { text: label; color: "#8b949e"; font.pixelSize: 18
                   Layout.alignment: Qt.AlignHCenter }
            Text { text: value.toFixed(1); color: "#58a6ff"
                   font.pixelSize: 64; font.bold: true
                   Layout.alignment: Qt.AlignHCenter }
            Text { text: unit; color: "#8b949e"; font.pixelSize: 16
                   Layout.alignment: Qt.AlignHCenter }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 20

        Rectangle {                       // status banner
            Layout.fillWidth: true
            Layout.preferredHeight: 76
            radius: 12
            color: statusColor(status)
            Behavior on color { ColorAnimation { duration: 300 } }

            RowLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 16
                Text {
                    text: status.toUpperCase()
                    color: "#0f1419"
                    font.pixelSize: 30
                    font.bold: true
                }
                Text {
                    text: reasons
                    color: "#0f1419"
                    font.pixelSize: 14
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignRight
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 20
            Tile { Layout.fillWidth: true; Layout.fillHeight: true
                   label: "Temperature"; value: temp;     unit: "°C" }
            Tile { Layout.fillWidth: true; Layout.fillHeight: true
                   label: "Pressure";    value: pressure; unit: "hPa" }
            Tile { Layout.fillWidth: true; Layout.fillHeight: true
                   label: "Humidity";    value: humidity; unit: "%" }
        }
    }
}