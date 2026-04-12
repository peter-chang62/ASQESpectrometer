<Global.Microsoft.VisualBasic.CompilerServices.DesignerGenerated()> _
Partial Class Form1
    Inherits System.Windows.Forms.Form

    'Form overrides dispose to clean up the component list.
    <System.Diagnostics.DebuggerNonUserCode()> _
    Protected Overrides Sub Dispose(ByVal disposing As Boolean)
        Try
            If disposing AndAlso components IsNot Nothing Then
                components.Dispose()
            End If
        Finally
            MyBase.Dispose(disposing)
        End Try
    End Sub

    'Required by the Windows Form Designer
    Private components As System.ComponentModel.IContainer

    'NOTE: The following procedure is required by the Windows Form Designer
    'It can be modified using the Windows Form Designer.  
    'Do not modify it using the code editor.
    <System.Diagnostics.DebuggerStepThrough()> _
    Private Sub InitializeComponent()
        Me.Button1 = New System.Windows.Forms.Button()
        Me.Button2 = New System.Windows.Forms.Button()
        Me.Label1 = New System.Windows.Forms.Label()
        Me.Button3 = New System.Windows.Forms.Button()
        Me.Button4 = New System.Windows.Forms.Button()
        Me.Button5 = New System.Windows.Forms.Button()
        Me.Button6 = New System.Windows.Forms.Button()
        Me.Button7 = New System.Windows.Forms.Button()
        Me.eraseFlashButton = New System.Windows.Forms.Button()
        Me.readFlashButton = New System.Windows.Forms.Button()
        Me.readFrameButton = New System.Windows.Forms.Button()
        Me.SuspendLayout()
        '
        'Button1
        '
        Me.Button1.Location = New System.Drawing.Point(37, 295)
        Me.Button1.Name = "Button1"
        Me.Button1.Size = New System.Drawing.Size(81, 23)
        Me.Button1.TabIndex = 0
        Me.Button1.Text = "Connect"
        Me.Button1.UseVisualStyleBackColor = True
        '
        'Button2
        '
        Me.Button2.Location = New System.Drawing.Point(224, 295)
        Me.Button2.Name = "Button2"
        Me.Button2.Size = New System.Drawing.Size(83, 23)
        Me.Button2.TabIndex = 2
        Me.Button2.Text = "Get status"
        Me.Button2.UseVisualStyleBackColor = True
        '
        'Label1
        '
        Me.Label1.AutoSize = True
        Me.Label1.Location = New System.Drawing.Point(34, 25)
        Me.Label1.Name = "Label1"
        Me.Label1.Size = New System.Drawing.Size(0, 13)
        Me.Label1.TabIndex = 3
        '
        'Button3
        '
        Me.Button3.Location = New System.Drawing.Point(313, 295)
        Me.Button3.Name = "Button3"
        Me.Button3.Size = New System.Drawing.Size(69, 23)
        Me.Button3.TabIndex = 4
        Me.Button3.Text = "Trigger"
        Me.Button3.UseVisualStyleBackColor = True
        '
        'Button4
        '
        Me.Button4.Location = New System.Drawing.Point(388, 295)
        Me.Button4.Name = "Button4"
        Me.Button4.Size = New System.Drawing.Size(75, 23)
        Me.Button4.TabIndex = 5
        Me.Button4.Text = "Get Frame"
        Me.Button4.UseVisualStyleBackColor = True
        '
        'Button5
        '
        Me.Button5.Location = New System.Drawing.Point(469, 295)
        Me.Button5.Name = "Button5"
        Me.Button5.Size = New System.Drawing.Size(89, 23)
        Me.Button5.TabIndex = 6
        Me.Button5.Text = "Clear memory"
        Me.Button5.UseVisualStyleBackColor = True
        '
        'Button6
        '
        Me.Button6.Location = New System.Drawing.Point(124, 295)
        Me.Button6.Name = "Button6"
        Me.Button6.Size = New System.Drawing.Size(93, 23)
        Me.Button6.TabIndex = 7
        Me.Button6.Text = "Get parameters"
        Me.Button6.UseVisualStyleBackColor = True
        '
        'Button7
        '
        Me.Button7.Location = New System.Drawing.Point(124, 324)
        Me.Button7.Name = "Button7"
        Me.Button7.Size = New System.Drawing.Size(93, 23)
        Me.Button7.TabIndex = 8
        Me.Button7.Text = "Set parameters"
        Me.Button7.UseVisualStyleBackColor = True
        '
        'eraseFlashButton
        '
        Me.eraseFlashButton.Location = New System.Drawing.Point(564, 295)
        Me.eraseFlashButton.Name = "eraseFlashButton"
        Me.eraseFlashButton.Size = New System.Drawing.Size(75, 23)
        Me.eraseFlashButton.TabIndex = 9
        Me.eraseFlashButton.Text = "Erase Flash"
        Me.eraseFlashButton.UseVisualStyleBackColor = True
        '
        'readFlashButton
        '
        Me.readFlashButton.Location = New System.Drawing.Point(564, 324)
        Me.readFlashButton.Name = "readFlashButton"
        Me.readFlashButton.Size = New System.Drawing.Size(75, 23)
        Me.readFlashButton.TabIndex = 10
        Me.readFlashButton.Text = "Read Flash"
        Me.readFlashButton.UseVisualStyleBackColor = True
        '
        'Button9
        '
        Me.readFrameButton.Location = New System.Drawing.Point(564, 353)
        Me.readFrameButton.Name = "Button9"
        Me.readFrameButton.Size = New System.Drawing.Size(75, 23)
        Me.readFrameButton.TabIndex = 11
        Me.readFrameButton.Text = "Write Flash"
        Me.readFrameButton.UseVisualStyleBackColor = True
        '
        'Form1
        '
        Me.AutoScaleDimensions = New System.Drawing.SizeF(6.0!, 13.0!)
        Me.AutoScaleMode = System.Windows.Forms.AutoScaleMode.Font
        Me.ClientSize = New System.Drawing.Size(703, 481)
        Me.Controls.Add(Me.readFrameButton)
        Me.Controls.Add(Me.readFlashButton)
        Me.Controls.Add(Me.eraseFlashButton)
        Me.Controls.Add(Me.Button7)
        Me.Controls.Add(Me.Button6)
        Me.Controls.Add(Me.Button5)
        Me.Controls.Add(Me.Button4)
        Me.Controls.Add(Me.Button3)
        Me.Controls.Add(Me.Label1)
        Me.Controls.Add(Me.Button2)
        Me.Controls.Add(Me.Button1)
        Me.Name = "Form1"
        Me.Text = "Form1"
        Me.ResumeLayout(False)
        Me.PerformLayout()

    End Sub
    Friend WithEvents Button1 As System.Windows.Forms.Button
    Friend WithEvents Button2 As System.Windows.Forms.Button
    Friend WithEvents Label1 As System.Windows.Forms.Label
    Friend WithEvents Button3 As System.Windows.Forms.Button
    Friend WithEvents Button4 As System.Windows.Forms.Button
    Friend WithEvents Button5 As System.Windows.Forms.Button
    Friend WithEvents Button6 As System.Windows.Forms.Button
    Friend WithEvents Button7 As System.Windows.Forms.Button
    Friend WithEvents eraseFlashButton As System.Windows.Forms.Button
    Friend WithEvents readFlashButton As System.Windows.Forms.Button
    Friend WithEvents readFrameButton As System.Windows.Forms.Button

End Class
