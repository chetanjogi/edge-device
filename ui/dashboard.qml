import QtQuick
import QtQuick.Window
import QtQuick.Layouts

Window {
    visible: true
    width: 800
    height: 480
    title: "Edge Device"
    color: "#0f1419"

    
    Component.onCompleted: {
        connected = device.isConnected();
        runState = device.currentState();
    }

    property real temp: 0
    property real pressure: 0
    property real humidity: 0
    property string status: "—"
    property string reasons: ""
    property string runState: "idle"
    property int pct: 0
    property bool connected: false
    property string lastError: ""
    property bool loggedIn: false
    property string loginError: ""

    function statusColor(s) {
        if (s === "normal")   return "#3fb950";
        if (s === "warning")  return "#d29922";
        if (s === "critical") return "#f85149";
        if (s === "invalid")  return "#6e7681";
        return "#30363d";
    }

    Connections {
        target: device
        function onConnectionChanged(ok) { connected = ok; }
        function onErrorOccurred(msg)    { lastError = msg; }
        function onReading(t, p, h, s, why) {
            temp = t; pressure = p; humidity = h; status = s; reasons = why;
        }
        function onStateChanged(s) { runState = s; }
        function onProgress(v)     { pct = v; }
        function onLoginResult(ok, msg) {
            loggedIn = ok;
            loginError = ok ? "" : msg;
        }
    }

    component Tile: Rectangle {
        property string label: ""
        property real value: 0
        property string unit: ""
        radius: 16
        color: "#1c2128"
        opacity: runState === "running" ? 1.0 : 0.45
        Behavior on opacity { NumberAnimation { duration: 250 } }
        ColumnLayout {
            anchors.centerIn: parent
            spacing: 2
            Text { text: label; color: "#8b949e"; font.pixelSize: 16
                   Layout.alignment: Qt.AlignHCenter }
            Text { text: value.toFixed(1); color: "#58a6ff"
                   font.pixelSize: 54; font.bold: true
                   Layout.alignment: Qt.AlignHCenter }
            Text { text: unit; color: "#8b949e"; font.pixelSize: 14
                   Layout.alignment: Qt.AlignHCenter }
        }
    }

    component TouchButton: Rectangle {
        property string label: ""
        property bool active: true
        signal tapped()
        Layout.preferredWidth: 200
        Layout.preferredHeight: 64        // big enough for a finger
        radius: 12
        color: active ? (ma.pressed ? "#2d5a8a" : "#1f6feb") : "#21262d"
        Behavior on color { ColorAnimation { duration: 120 } }
        Text {
            anchors.centerIn: parent
            text: label
            color: active ? "white" : "#484f58"
            font.pixelSize: 20
            font.bold: true
        }
        MouseArea {
            id: ma
            anchors.fill: parent
            enabled: active
            onClicked: tapped()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 14

        // Status banner
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 68
            radius: 12
            color: runState === "running" ? statusColor(status) : "#30363d"
            Behavior on color { ColorAnimation { duration: 300 } }
            RowLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 12
                Rectangle {
                    width: 14; height: 14; radius: 7
                    color: connected ? "#3fb950" : "#f85149"
                    Behavior on color { ColorAnimation { duration: 200 } }
                }
                Text {
                    text: runState.toUpperCase() +
                          (runState === "running" ? " — " + status.toUpperCase() : "")
                    color: runState === "running" ? "#0f1419" : "#c9d1d9"
                    font.pixelSize: 26; font.bold: true
                }
                Text {
                    text: reasons
                    color: runState === "running" ? "#0f1419" : "#8b949e"
                    font.pixelSize: 13
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    horizontalAlignment: Text.AlignRight
                }
            }
        }

        // Progress bar
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 10
            radius: 5
            color: "#21262d"
            Rectangle {
                width: parent.width * (pct / 100)
                height: parent.height
                radius: 5
                color: "#1f6feb"
                Behavior on width { NumberAnimation { duration: 200 } }
            }
        }

        // Live tiles
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16
            Tile { Layout.fillWidth: true; Layout.fillHeight: true
                   label: "Temperature"; value: temp;     unit: "°C" }
            Tile { Layout.fillWidth: true; Layout.fillHeight: true
                   label: "Pressure";    value: pressure; unit: "hPa" }
            Tile { Layout.fillWidth: true; Layout.fillHeight: true
                   label: "Humidity";    value: humidity; unit: "%" }
        }

        // Operator controls — enabled state comes straight from the FSM
        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: 16
            TouchButton {
                label: "START"
                active: runState === "idle"
                onTapped: device.startRun()
            }
            TouchButton {
                label: "ABORT"
                active: runState === "running"
                onTapped: device.abortRun()
            }
            TouchButton {
                label: "RESET"
                active: runState === "completed" || runState === "failed"
                onTapped: device.resetRun()
            }
        }
    }
    Rectangle {
        anchors.fill: parent
        color: "#0f1419"
        visible: !loggedIn
        z: 100

        Column {
            anchors.centerIn: parent
            spacing: 16
            width: 320

            Text {
                text: "Edge Device — Sign In"
                color: "#c9d1d9"; font.pixelSize: 24; font.bold: true
                anchors.horizontalCenter: parent.horizontalCenter
            }

            Rectangle {
                width: parent.width; height: 48; radius: 8; color: "#1c2128"
                border.color: userField.activeFocus ? "#1f6feb" : "#30363d"
                TextInput {
                    id: userField
                    anchors.fill: parent; anchors.margins: 14
                    color: "white"; font.pixelSize: 18
                    verticalAlignment: TextInput.AlignVCenter
                    KeyNavigation.tab: passField
                    Text {
                        text: "username"; color: "#6e7681"; font.pixelSize: 18
                        visible: !parent.text && !parent.activeFocus
                    }
                }
            }

            Rectangle {
                width: parent.width; height: 48; radius: 8; color: "#1c2128"
                border.color: passField.activeFocus ? "#1f6feb" : "#30363d"
                TextInput {
                    id: passField
                    anchors.fill: parent; anchors.margins: 14
                    color: "white"; font.pixelSize: 18
                    echoMode: TextInput.Password
                    verticalAlignment: TextInput.AlignVCenter
                    onAccepted: device.login(userField.text, passField.text)
                    Text {
                        text: "password"; color: "#6e7681"; font.pixelSize: 18
                        visible: !parent.text && !parent.activeFocus
                    }
                }
            }

            Rectangle {
                width: parent.width; height: 48; radius: 8
                color: loginArea.pressed ? "#2d5a8a" : "#1f6feb"
                Text {
                    anchors.centerIn: parent; text: "SIGN IN"
                    color: "white"; font.pixelSize: 18; font.bold: true
                }
                MouseArea {
                    id: loginArea; anchors.fill: parent
                    onClicked: device.login(userField.text, passField.text)
                }
            }

            Text {
                text: loginError; color: "#f85149"; font.pixelSize: 14
                anchors.horizontalCenter: parent.horizontalCenter
                visible: loginError !== ""
            }
        }
    }
}