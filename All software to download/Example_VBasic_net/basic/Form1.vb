

Public Class Form1

    Private Sub Form1_Click(sender As Object, e As EventArgs) Handles MyBase.Click

    End Sub

    Private Sub Button1_Click(sender As Object, e As EventArgs) Handles Button1.Click
        Dim result As Integer
        result = spectInterface.connectToDeviceByIndex(0)
        If result = 0 Then
            Label1.Text += "Connection to device established" + vbCrLf
        Else
            Label1.Text += "Can't connect to device. Error code: " + result.ToString + vbCrLf

        End If

    End Sub



    Private Sub Button2_Click(sender As Object, e As EventArgs) Handles Button2.Click
        Dim result As Integer
        Dim status As Byte
        Dim spectres As UShort


        result = spectInterface.getStatus(status, spectres)

        If result = 0 Then
            Label1.Text += "status flags= " + status.ToString + " frames in memory: " + spectres.ToString + vbCrLf
        Else
            Label1.Text += "Can't get status  Error code: " + result.ToString + vbCrLf

        End If

      

    End Sub

    Private Sub Button3_Click(sender As Object, e As EventArgs) Handles Button3.Click
        spectInterface.triggerAcquisition()
    End Sub

    Private Sub Form1_Load(sender As Object, e As EventArgs) Handles MyBase.Load
        Randomize()
    End Sub

    Private Sub Button6_Click(sender As Object, e As EventArgs) Handles Button6.Click
        Dim numScans As UShort
        Dim numBlankScans As UShort
        Dim scanMode As Byte
        Dim exposure As UInteger
        Dim startElement As UShort
        Dim endElement As UShort
        Dim reductionMode As Byte
        Dim pixelsInFrame As UShort
        Dim result As Integer
        result = spectInterface.getAcquisitionParameters(numScans, numBlankScans, scanMode, exposure)

        If result = 0 Then
            Label1.Text += "numScans: " + numScans.ToString + " numBlankScans: " + numBlankScans.ToString + " scanMode: " + scanMode.ToString + " Exposure: " + exposure.ToString + vbCrLf
        Else
            Label1.Text += "Can't get Acquisition Parameters  Error code: " + result.ToString + vbCrLf

        End If
        result = spectInterface.getFrameFormat(startElement, endElement, reductionMode, pixelsInFrame)

        If result = 0 Then
            Label1.Text += "startElement:  " + startElement.ToString + " endElement: " + endElement.ToString + " reductionMode: " + reductionMode.ToString + " pixelsInFrame: " + pixelsInFrame.ToString + vbCrLf
        Else
            Label1.Text += "Can't get frame format  Error code: " + result.ToString + vbCrLf

        End If

    End Sub

    Private Sub Button7_Click(sender As Object, e As EventArgs) Handles Button7.Click
        Dim result As Integer
        Dim pixelsInFrame As UShort
        result = spectInterface.setAcquisitionParameters(10, 10, 1, 20)
        If result = 0 Then
            Label1.Text += "Parameters set ok" + vbCrLf
        Else
            Label1.Text += "Can't set parameters.  Error code: " + result.ToString + vbCrLf

        End If


        result = spectInterface.setFrameFormat(100, 2500, 0, pixelsInFrame)
        If result = 0 Then
            Label1.Text += "Frame format set ok" + vbCrLf
        Else
            Label1.Text += "Can't set frame format.  Error code: " + result.ToString + vbCrLf

        End If


    End Sub

    Private Sub Button5_Click(sender As Object, e As EventArgs) Handles Button5.Click
        spectInterface.clearMemory()
    End Sub

    Private Sub Button4_Click(sender As Object, e As EventArgs) Handles Button4.Click
        Dim spectra(4000) As UShort
        Dim result As Integer

        result = spectInterface.getFrame(spectra, 0)

        If result = 0 Then

            Label1.Text += "Spectra = "

            For i As Integer = 0 To 10 Step 1
                Label1.Text += spectra(i).ToString + ", "
            Next
            Label1.Text += "..." + vbCrLf


        Else
            Label1.Text += "Can't get spectra  Error code: " + result.ToString + vbCrLf

        End If


    End Sub

    Private Sub eraseFlashButton_Click(sender As Object, e As EventArgs) Handles eraseFlashButton.Click
        Dim result As Integer

        result = spectInterface.eraseFlash()

        If result = 0 Then
            Label1.Text += "Flash was successfully erased" + vbCrLf
        Else
            Label1.Text += "Cannot erase flash. Error code: " + result.ToString + vbCrLf
        End If

    End Sub

    Private Sub readFlashButton_Click(sender As Object, e As EventArgs) Handles readFlashButton.Click
        Dim result As Integer
        Dim absoluteOffset As UInteger
        Dim bytesToRead As UInteger
        Dim flash() As Byte

        bytesToRead = 10000
        absoluteOffset = 0
        flash = New Byte(bytesToRead) {}

        result = spectInterface.readFlash(flash, absoluteOffset, bytesToRead)

        If result = 0 Then
            Label1.Text += "Flash (offset: " + absoluteOffset.ToString() + ", bytes read: " + bytesToRead.ToString() + "): "

            For i As Integer = 0 To 10 Step 1
                Label1.Text += flash(i).ToString + ", "
            Next
            Label1.Text += "..." + vbCrLf
        Else
            Label1.Text += "Cannot read flash. Error code: " + result.ToString + vbCrLf

        End If

    End Sub

    Private Sub writeFlashButton_Click(sender As Object, e As EventArgs) Handles readFrameButton.Click
        Dim result As Integer
        Dim absoluteOffset As UInteger
        Dim bytesToWrite As UInteger
        Dim flash() As Byte

        bytesToWrite = 10000
        absoluteOffset = 0
        flash = New Byte(bytesToWrite) {}

        For i As Integer = 0 To bytesToWrite Step 1
            flash(i) = CInt(134)
        Next

        result = spectInterface.writeFlash(flash, absoluteOffset, bytesToWrite)

        If result = 0 Then
            Label1.Text += "Flash was successfully written to with " + bytesToWrite.ToString() + " bytes starting with offset " + absoluteOffset.ToString() + vbCrLf
        Else
            Label1.Text += "Cannot read flash. Error code: " + result.ToString + vbCrLf
        End If


    End Sub
End Class
