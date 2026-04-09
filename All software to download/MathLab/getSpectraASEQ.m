% The following code acquires a single frame from a ASEQ spectrometer and
% wright the wavelength in [nm] to the line array waveLength and the
% corresponding Intensity to the line array Int in [a.u.].

% The function was only tested with HR1 spectrometers. Using different
% spectrometers may require modifications to the code and the calibration
% file.
 
% In order for this function to work, the calibrationFile.txt, libspectr.h,
% spectrlib_core_shared.ddl as well as the spectrlib_core_shared.lib file
% (providet by ASEQ instuments) must be saved in the same folder as this 
% function.

function [waveLength,Int] = getSpectraASEQ(numOfScans,numOfBlankScans,exposureTime)

if not (libisloaded('spectrlib_core_shared'))                                                       %% checks if library is loaded 
    loadlibrary('spectrlib_core_shared','libspectr.h')                                              %% loads library    
end
%libfunctions('spectrlib_core_shared')                                                               %% displays functions in library 
%libfunctionsview spectrlib_core_shared                                                              %% displays datatypes of functions                                                           

[DevicesCount]=calllib('spectrlib_core_shared','getDevicesCount');                                  %% number of devices 

Index = 0;                                                                                          %% set Index of device; Index = [0;DevicesCount-1]                                                                                                   
                                                                                                    
x = textread('CalibrationFile.txt');                                                                %% reads calibrationFile.text, remove first line of the original Calibration file and convert to .txt
waveLength = x(2:3654);                                                                             %% crops the calibration file to needed data. Units: [nm]
                                                                                                                                                                                           
[connection,deviceContextPtr]=calllib('spectrlib_core_shared','connectToDeviceByIndex',Index,0);    %% connect To device

numOfStartElement = 0;                                                                              %% defines start wavelength
numOfEndElement = 3647;                                                                             %% defines end wavelength
reductionMode = 0;                                                                                  %% ????

numOfScans = numOfScans;                                                                            %% defines number of Scans, end result will be the integration of all scans
numOfBlankScans = numOfBlankScans;                                                                  %% 
scanMode = 3;                                                                                       %% defines scan mode, only 3 is valid 
exposureTime = exposureTime;                                                                        %% exposure time in multiples of us

numOfPixelsInFrame = 0;                                                                             %% needed for later operations %% do not edit %%
StatusFlags = 0;                                                                                    %% needed for later operations %% do not edit %%
framesInMemory = 0;                                                                                 %% needed for later operations %% do not edit %%
numOfFrame = 65535;                                                                                 %% needed for later operations %% do not edit %%
Buffer = libpointer('uint16Ptr',zeros(3694,1));                                                     %% buffer for data acquisition

[connection,deviceContextPtr]=calllib('spectrlib_core_shared','setAcquisitionParameters',...        %% set the acquisition parameter
    numOfScans,numOfBlankScans,scanMode,exposureTime,deviceContextPtr);                             %% set the acquisition parameter

[connection,numOfScans,numOfBlankScans,scanMode,exposureTime,deviceContextPtr]=calllib('spectrlib_core_shared','getAcquisitionParameters',...   
    numOfScans,numOfBlankScans,scanMode,exposureTime,deviceContextPtr);                                                                         

[connection,numOfPixelsInFrame,deviceContextPtr]=calllib('spectrlib_core_shared','setFrameFormat',...        %% set the frame format
   numOfStartElement,numOfEndElement,reductionMode,numOfPixelsInFrame,deviceContextPtr);                     %% set the frame format

[connection,numOfStartElement,numOfEndElement,reductionMode,numOfPixelsInFrame,deviceContextPtr]=calllib('spectrlib_core_shared','getFrameFormat',...
   numOfStartElement,numOfEndElement,reductionMode,numOfPixelsInFrame,deviceContextPtr);

[connection,StatusFlags,framesInMemory,deviceContextPtr]=calllib('spectrlib_core_shared','getStatus',...    %% checks for frames in memory
    StatusFlags,framesInMemory,deviceContextPtr);                                                           %% checks for frames in memory

connection = 0;

while (framesInMemory ==0)                                                                                  %% continues if frame is in memory
    pause(0.025)
    
    [connection,StatusFlags,framesInMemory,deviceContextPtr]=calllib('spectrlib_core_shared','getStatus',...
    StatusFlags,framesInMemory,deviceContextPtr);
    connection = connection+1;
end                                                                                                         %% continues if frame is in memory

[connection,frame,deviceContextPtr]=calllib('spectrlib_core_shared','getFrame',...                          %% reads frame from memory
    Buffer,numOfFrame,deviceContextPtr);                                                                    %% reads frame from memory

Int = frame(32:3684);                                                                                       %% dumps dummy values 

[connection,deviceContextPtr]=calllib('spectrlib_core_shared','disconnectDeviceContext',...         %% disconnect 
    deviceContextPtr);                                                                              %% disconnect

unloadlibrary spectrlib_core_shared                                                                 %% unload library 
end

%Code wrighten by: Tillmann Spellauge of the Multiphoton Imaging Lab at
%                  Munich University of Applied Sciences
%                  Department of
%                  Applied Sciences and Mechatronics
%                  Lothstr. 34, 80335 München