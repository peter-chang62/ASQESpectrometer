Imports System.Runtime.InteropServices

Public Class spectInterface

    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function connectToDeviceByIndex(ByVal index As UInteger) As Integer
    End Function
    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function setAcquisitionParameters(numOfScans As UShort, numOfBlankScans As UShort, scanMode As Byte, timeOfExposure As UInteger) As Integer
    End Function
    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function getStatus(ByRef statusFlags As Byte, ByRef framesInMemory As UShort) As Integer
    End Function
    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function getFrameFormat(ByRef numOfStartElement As UShort, ByRef numOfEndElement As UShort, ByRef reductionMode As Byte, ByRef numOfPixelsInFrame As UShort) As Integer
    End Function
    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function setFrameFormat(numOfStartElement As UShort, numOfEndElement As UShort, reductionMode As Byte, ByRef numOfPixelsInFrame As UShort) As Integer
    End Function
    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function triggerAcquisition() As Integer
    End Function
    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function clearMemory() As Integer
    End Function

    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function getFrame(framePixelsBuffer() As UShort, numOfFrame As UShort) As Integer
    End Function
    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function getAcquisitionParameters(ByRef numOfScans As UShort, ByRef numOfBlankScans As UShort, ByRef scanMode As Byte, ByRef timeOfExposure As UInteger) As Integer
    End Function

    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function eraseFlash() As Integer
    End Function
    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function readFlash(buffer() As Byte, absoluteOffset As UInteger, bytesToRead As UInteger) As Integer
    End Function
    <DllImport("spectrlib_shared.dll", CallingConvention:=CallingConvention.Cdecl)> _
    Public Shared Function writeFlash(buffer() As Byte, absoluteOffset As UInteger, bytesToRead As UInteger) As Integer
    End Function

End Class